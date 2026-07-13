from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from .comfy_runtime import sha256_file
from .config import Settings
from .database import Database, utc_now
from .engine import EngineService
from .schemas import PoseImportInput

POSE_WORKFLOW_VERSION = "pose-openpose-sdxl-v1"
PREPROCESSOR_REVISION = "comfyui_controlnet_aux:managed-openpose"
CONTROL_LORA_FILENAME = "control-lora-openposeXL2-rank256.safetensors"


class PoseService:
    def __init__(self, db: Database, settings: Settings, engine: EngineService):
        self.db, self.settings, self.engine = db, settings, engine

    def list(self, query: str = "") -> list[dict]:
        sql, params = "SELECT * FROM pose_assets", ()
        if query.strip():
            sql, params = f"{sql} WHERE name LIKE ? OR tags LIKE ?", (f"%{query}%", f"%{query}%")
        rows = self.db.query_all(f"{sql} ORDER BY favorite DESC, updated_at DESC", params)
        for row in rows:
            row["tags"] = json.loads(row["tags"])
            row["favorite"] = bool(row["favorite"])
            row["crop_settings"] = json.loads(row["crop_settings"])
        return rows

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
        self.engine.runtime.start()
        if not self.engine.runtime.wait_healthy(timeout=45):
            raise ValueError("Start the Local Generation Engine before extracting a pose")
        nodes = self.engine.runtime._request_json("/object_info")
        if "OpenposePreprocessor" not in nodes:
            raise ValueError("Vanta's managed OpenPose preprocessor is not installed")
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
        layout = self.engine.runtime.installed_layout()
        if layout is None:
            raise ValueError("The Local Generation Engine is not installed")
        input_dir = layout[0].parent / "input" / "Vanta"
        input_dir.mkdir(parents=True, exist_ok=True)
        input_name = f"{pose_id}.jpg"
        shutil.copy2(original, input_dir / input_name)
        workflow = {
            "1": {"class_type": "LoadImage", "inputs": {"image": f"Vanta/{input_name}"}},
            "2": {
                "class_type": "OpenposePreprocessor",
                "inputs": {
                    "image": ["1", 0],
                    "detect_hand": "enable",
                    "detect_body": "enable",
                    "detect_face": "enable",
                    "resolution": 512,
                },
            },
            "3": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": f"Vanta/pose-{pose_id}", "images": ["2", 0]},
            },
        }
        _, history = self.engine.runtime.submit(workflow, lambda _step, _maximum: None)
        output = history.get("outputs", {}).get("3", {}).get("images", [])
        if not output:
            raise ValueError("The local OpenPose preprocessor produced no control image")
        produced = output[0]
        control_source = (
            self.engine.runtime.root
            / "output"
            / produced.get("subfolder", "")
            / produced["filename"]
        )
        if not control_source.is_file():
            raise ValueError("The local OpenPose control image is missing")
        control = root / "control.png"
        control_thumbnail = root / "control.thumb.jpg"
        shutil.copy2(control_source, control)
        with Image.open(control) as image:
            image.thumbnail((480, 480))
            image.convert("RGB").save(control_thumbnail, "JPEG", quality=88, optimize=True)
        self.db.execute(
            """INSERT INTO pose_assets(id, name, scope, character_id, source_path, source_thumbnail_path, control_path, control_thumbnail_path, source_sha256, control_sha256, tags, favorite, notes, crop_settings, strength, preprocessor_revision, workflow_pack_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?)""",
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
                sha256_file(control),
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
        return self.list()[0]
