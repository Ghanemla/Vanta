from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from .comfy_runtime import sha256_file
from .config import Settings
from .database import Database, utc_now
from .engine import EngineService
from .schemas import PoseImportInput, PoseUpdateInput

POSE_WORKFLOW_VERSION = "pose-dwpose-sdxl-v1"
PREPROCESSOR_REVISION = "comfyui_controlnet_aux:e8b689a:dwpose"
CONTROL_LORA_FILENAME = "xinsir-openpose-sdxl-1.0.safetensors"


class PoseService:
    def __init__(self, db: Database, settings: Settings, engine: EngineService):
        self.db, self.settings, self.engine = db, settings, engine
        self._lock = threading.Lock()
        self._workers: dict[str, threading.Thread] = {}
        self.db.execute(
            "UPDATE pose_assets SET status='failed', error_message='Vanta closed before pose extraction finished' "
            "WHERE status IN ('queued', 'starting', 'extracting', 'saving')"
        )

    @staticmethod
    def _decode(row: dict) -> dict:
        row["tags"] = json.loads(row["tags"])
        row["favorite"] = bool(row["favorite"])
        row["crop_settings"] = json.loads(row["crop_settings"])
        return row

    def list(self, query: str = "") -> list[dict]:
        sql, params = "SELECT * FROM pose_assets", ()
        if query.strip():
            sql, params = f"{sql} WHERE name LIKE ? OR tags LIKE ?", (f"%{query}%", f"%{query}%")
        rows = self.db.query_all(f"{sql} ORDER BY favorite DESC, updated_at DESC", params)
        return [self._decode(row) for row in rows]

    def get(self, pose_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM pose_assets WHERE id=?", (pose_id,))
        if row is None:
            raise KeyError(pose_id)
        return self._decode(row)

    def import_pose(self, payload: PoseImportInput) -> dict:
        source = Path(payload.source_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("Choose an existing local pose-reference image")
        try:
            with Image.open(source) as probe:
                probe.verify()
            with Image.open(source) as image:
                rendered = ImageOps.exif_transpose(image).convert("RGB")
                if min(rendered.size) < 256:
                    raise ValueError("Choose a pose reference at least 256 pixels on each side")
        except (OSError, ValueError) as error:
            raise ValueError("Vanta could not read this pose reference") from error
        if (
            payload.character_id
            and self.db.query_one("SELECT id FROM characters WHERE id=?", (payload.character_id,))
            is None
        ):
            raise ValueError("The selected character no longer exists")
        pose_id, now = f"pose-{uuid.uuid4().hex}", utc_now()
        root = self.settings.pose_root / pose_id
        root.mkdir(parents=True, exist_ok=True)
        original = root / "source.jpg"
        with Image.open(source) as image:
            rendered = ImageOps.exif_transpose(image).convert("RGB")
            rendered.save(original, "JPEG", quality=94, optimize=True)
        source_thumbnail = root / "source.thumb.jpg"
        with Image.open(original) as image:
            image.thumbnail((480, 480))
            image.save(source_thumbnail, "JPEG", quality=88, optimize=True)
        control = root / "control.png"
        control_thumbnail = root / "control.thumb.jpg"
        self.db.execute(
            """INSERT INTO pose_assets(id, name, scope, character_id, source_path, source_thumbnail_path, control_path, control_thumbnail_path, source_sha256, control_sha256, tags, favorite, notes, crop_settings, strength, preprocessor_revision, workflow_pack_version, created_at, updated_at, status, progress)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, '{}', ?, ?, ?, ?, ?, 'queued', 5)""",
            (
                pose_id,
                payload.name,
                "character" if payload.character_id else "global",
                payload.character_id,
                str(original),
                str(source_thumbnail),
                str(control),
                str(control_thumbnail),
                sha256_file(original),
                json.dumps(payload.tags),
                int(payload.favorite),
                payload.notes,
                payload.strength,
                PREPROCESSOR_REVISION,
                POSE_WORKFLOW_VERSION,
                now,
                now,
            ),
        )
        worker = threading.Thread(target=self._extract, args=(pose_id,), daemon=True)
        with self._lock:
            self._workers[pose_id] = worker
        worker.start()
        return self.get(pose_id)

    def _update_progress(
        self, pose_id: str, status: str, progress: int, error: str | None = None
    ) -> None:
        self.db.execute(
            "UPDATE pose_assets SET status=?, progress=?, error_message=?, updated_at=? WHERE id=?",
            (status, progress, error, utc_now(), pose_id),
        )

    def _extract(self, pose_id: str) -> None:
        try:
            pose = self.get(pose_id)
            self._update_progress(pose_id, "starting", 12)
            layout = self.engine.runtime.installed_layout()
            if layout is None:
                raise ValueError("Install the Local Generation Engine before extracting a pose")
            self.engine.runtime.start()
            if not self.engine.runtime.wait_healthy(timeout=45):
                raise ValueError(
                    "Start or repair the Local Generation Engine before extracting a pose"
                )
            nodes = self.engine.runtime._request_json("/object_info")
            if "DWPreprocessor" not in nodes:
                raise ValueError("Install and verify Vanta Pose Control before extracting a pose")
            input_dir = layout[0].parent / "input" / "Vanta"
            input_dir.mkdir(parents=True, exist_ok=True)
            input_name = f"{pose_id}.jpg"
            shutil.copy2(pose["source_path"], input_dir / input_name)
            self._update_progress(pose_id, "extracting", 25)
            workflow = self._extraction_workflow(pose_id, input_name)

            def progress(value: int, maximum: int) -> None:
                self._update_progress(
                    pose_id, "extracting", min(84, 25 + round(59 * value / max(maximum, 1)))
                )

            _, history = self.engine.runtime.submit(workflow, progress)
            output = history.get("outputs", {}).get("3", {}).get("images", [])
            if not output:
                raise ValueError("The local pose preprocessor produced no control image")
            produced = output[0]
            control_source = (
                self.engine.runtime.root
                / "output"
                / produced.get("subfolder", "")
                / produced["filename"]
            )
            if not control_source.is_file():
                raise ValueError("The local pose control image is missing")
            self._update_progress(pose_id, "saving", 90)
            control = Path(pose["control_path"])
            control_thumbnail = Path(pose["control_thumbnail_path"])
            shutil.copy2(control_source, control)
            with Image.open(control) as image:
                image.thumbnail((480, 480))
                image.convert("RGB").save(control_thumbnail, "JPEG", quality=88, optimize=True)
            self.db.execute(
                "UPDATE pose_assets SET control_sha256=?, status='ready', progress=100, error_message=NULL, updated_at=? WHERE id=?",
                (sha256_file(control), utc_now(), pose_id),
            )
        except Exception as error:
            self._update_progress(pose_id, "failed", 0, str(error))
        finally:
            with self._lock:
                self._workers.pop(pose_id, None)

    @staticmethod
    def _extraction_workflow(pose_id: str, input_name: str) -> dict:
        workflow = {
            "1": {"class_type": "LoadImage", "inputs": {"image": f"Vanta/{input_name}"}},
            "2": {
                "class_type": "DWPreprocessor",
                "inputs": {
                    "image": ["1", 0],
                    "detect_hand": "enable",
                    "detect_body": "enable",
                    "detect_face": "enable",
                    "bbox_detector": "yolox_l.onnx",
                    "pose_estimator": "dw-ll_ucoco_384.onnx",
                    "scale_stick_for_xinsr_cn": "enable",
                    "resolution": 512,
                },
            },
            "3": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": f"Vanta/pose-{pose_id}", "images": ["2", 0]},
            },
        }
        return workflow

    def update(self, pose_id: str, payload: PoseUpdateInput) -> dict:
        self.get(pose_id)
        if (
            payload.character_id
            and self.db.query_one("SELECT id FROM characters WHERE id=?", (payload.character_id,))
            is None
        ):
            raise ValueError("The selected character no longer exists")
        self.db.execute(
            """UPDATE pose_assets SET name=?, scope=?, character_id=?, tags=?, favorite=?, notes=?, strength=?, updated_at=? WHERE id=?""",
            (
                payload.name,
                "character" if payload.character_id else "global",
                payload.character_id,
                json.dumps(payload.tags),
                int(payload.favorite),
                payload.notes,
                payload.strength,
                utc_now(),
                pose_id,
            ),
        )
        return self.get(pose_id)

    def duplicate(self, pose_id: str) -> dict:
        source = self.get(pose_id)
        duplicate = PoseImportInput(
            name=f"{source['name']} — Copy",
            source_path=source["source_path"],
            tags=source["tags"],
            favorite=False,
            notes=source["notes"],
            character_id=source["character_id"],
            strength=source["strength"],
        )
        return self.import_pose(duplicate)

    def delete(self, pose_id: str) -> None:
        item = self.get(pose_id)
        for field in (
            "source_path",
            "source_thumbnail_path",
            "control_path",
            "control_thumbnail_path",
        ):
            Path(item[field]).unlink(missing_ok=True)
        self.db.execute("DELETE FROM pose_assets WHERE id=?", (pose_id,))
        root = self.settings.pose_root / pose_id
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
