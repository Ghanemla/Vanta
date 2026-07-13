from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageStat

from .comfy_runtime import ensure_safe_archive_members, sha256_file
from .config import Settings
from .database import Database, utc_now
from .engine import EngineService
from .repositories import LoraRepository
from .schemas import (
    CharacterLoraInput,
    LoraImportInput,
    TrainingDatasetInput,
    TrainingInstallInput,
    TrainingRunInput,
)

logger = logging.getLogger("vanta.orchestrator.training")

ACTIVE_STATES = {"queued", "preparing", "training", "cancelling"}
TRAINING_PROFILES: dict[str, dict[str, Any]] = {
    "safe_12gb": {
        "display_name": "Safe 12 GB",
        "resolution": 512,
        "rank": 4,
        "alpha": 4,
        "repeats": 4,
        "gradient_accumulation": 1,
        "vram_gb": 10.5,
        "seconds_per_step": 18,
        "disk_gb": 2.2,
    },
    "balanced_12gb": {
        "display_name": "Balanced 12 GB",
        "resolution": 768,
        "rank": 8,
        "alpha": 8,
        "repeats": 6,
        "gradient_accumulation": 2,
        "vram_gb": 11.7,
        "seconds_per_step": 28,
        "disk_gb": 3.8,
    },
}


def _safe_error(error: object) -> str:
    message = " ".join(str(error).replace("\r", " ").replace("\n", " ").split())
    return message[:1000] or "The local training operation failed"


def _average_hash(image: Image.Image) -> str:
    pixels = list(image.convert("L").resize((8, 8), Image.Resampling.LANCZOS).getdata())
    average = sum(pixels) / len(pixels)
    bits = "".join("1" if value >= average else "0" for value in pixels)
    return f"{int(bits, 2):016x}"


def _hash_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _blur_score(image: Image.Image) -> float:
    edges = (
        image.convert("L")
        .resize((256, 256), Image.Resampling.BILINEAR)
        .filter(ImageFilter.FIND_EDGES)
    )
    return round(float(ImageStat.Stat(edges).var[0]), 2)


class TrainingService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        engine: EngineService,
        loras: LoraRepository,
    ) -> None:
        self.db = db
        self.settings = settings
        self.engine = engine
        self.loras = loras
        self._component_threads: dict[str, threading.Thread] = {}
        self._run_threads: dict[str, threading.Thread] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()
        self._recover()
        self.sync_components()

    @property
    def _trainer_manifest(self):
        return next(item for item in self.engine.core.components if item.id == "lora-training")

    @property
    def _caption_manifest(self):
        return next(item for item in self.engine.core.components if item.id == "captioning")

    @property
    def _trainer_source(self) -> Path:
        return self.settings.trainer_runtime_root / "source"

    @property
    def _trainer_packages(self) -> Path:
        return self.settings.trainer_runtime_root / "packages"

    @property
    def _trainer_tokenizers(self) -> Path:
        return self.settings.trainer_runtime_root / "tokenizers"

    @property
    def _caption_packages(self) -> Path:
        return self.settings.captioning_root / "packages"

    @property
    def _training_runner(self) -> Path:
        return self.settings.engine_manifest_dir.parent / "tools" / "vanta_training_runner.py"

    @property
    def _caption_runner(self) -> Path:
        return self.settings.engine_manifest_dir.parent / "tools" / "vanta_caption_runner.py"

    def _python(self) -> Path:
        layout = self.engine.runtime.installed_layout()
        if layout is None:
            raise ValueError("Install the Local Generation Engine before local training")
        return layout[1]

    def _component_progress(self, item_id: str, progress: int, message: str) -> None:
        self.db.execute(
            "UPDATE engine_components SET state='installing', progress=?, last_health_message=?, updated_at=? WHERE id=?",
            (progress, message, utc_now(), item_id),
        )

    def _set_component(self, item_id: str, state: str, message: str) -> None:
        self.db.execute(
            "UPDATE engine_components SET state=?, progress=?, last_health_message=?, updated_at=? WHERE id=?",
            (state, 100 if state == "ready" else 0, message, utc_now(), item_id),
        )

    def sync_components(self) -> None:
        trainer_marker = self.settings.trainer_runtime_root / "installation.json"
        trainer_ready = (
            trainer_marker.is_file()
            and (self._trainer_source / "sdxl_train_network.py").is_file()
            and (self._trainer_packages / "accelerate").is_dir()
            and all(
                (self._trainer_tokenizers / str(item["cache_name"]) / "vocab.json").is_file()
                for item in self._trainer_manifest.source["tokenizers"]
            )
            and self._training_runner.is_file()
        )
        trainer_thread = self._component_threads.get("lora-training")
        if not trainer_thread or not trainer_thread.is_alive():
            self._set_component(
                "lora-training",
                "ready" if trainer_ready else "not_installed",
                "Pinned sd-scripts trainer and isolated CUDA dependencies are verified"
                if trainer_ready
                else "Install the pinned local sd-scripts trainer",
            )
        source = self._caption_manifest.source
        model = self.settings.captioning_root / str(source["model"]["filename"])
        tags = self.settings.captioning_root / str(source["tags"]["filename"])
        caption_ready = (
            (self.settings.captioning_root / "installation.json").is_file()
            and model.is_file()
            and model.stat().st_size == int(source["model"]["bytes"])
            and tags.is_file()
            and tags.stat().st_size == int(source["tags"]["bytes"])
            and (self._caption_packages / "onnxruntime").is_dir()
            and self._caption_runner.is_file()
        )
        caption_thread = self._component_threads.get("captioning")
        if not caption_thread or not caption_thread.is_alive():
            self._set_component(
                "captioning",
                "ready" if caption_ready else "not_installed",
                "Pinned local ONNX captioner is verified; character-name tags are excluded"
                if caption_ready
                else "Install the pinned local image captioner",
            )

    def component_action(self, item_id: str, action: str) -> dict[str, Any]:
        if item_id not in {"lora-training", "captioning"}:
            raise KeyError(item_id)
        if action == "verify":
            action = "health_check"
        if action == "update":
            manifest = (
                self._trainer_manifest if item_id == "lora-training" else self._caption_manifest
            )
            row = self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,))
            if row and row["manifest_version"] == manifest.version:
                self.db.execute(
                    "UPDATE engine_components SET last_health_message=?,updated_at=? WHERE id=?",
                    (f"Already current at {manifest.version}", utc_now(), item_id),
                )
                return (
                    self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,))
                    or {}
                )
            action = "repair"
        if action in {"install", "repair"}:
            thread = self._component_threads.get(item_id)
            if thread is None or not thread.is_alive():
                self._component_progress(item_id, 1, f"Preparing {item_id.replace('-', ' ')}")
                target = (
                    self._install_trainer if item_id == "lora-training" else self._install_captioner
                )
                thread = threading.Thread(target=target, daemon=True)
                self._component_threads[item_id] = thread
                thread.start()
        elif action == "health_check":
            if item_id == "lora-training":
                self._verify_trainer()
            else:
                self._verify_captioner()
        elif action == "remove":
            root = (
                self.settings.trainer_runtime_root
                if item_id == "lora-training"
                else self.settings.captioning_root
            )
            shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True, exist_ok=True)
            self._set_component(
                item_id, "not_installed", f"{item_id.replace('-', ' ').title()} removed"
            )
        else:
            raise ValueError("Use Install, Verify, Repair, or Remove for this capability")
        return self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,)) or {}

    def _runner_environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "WANDB_MODE": "disabled",
        }

    def _install_packages(self, package_root: Path, packages: list[str]) -> None:
        package_root.mkdir(parents=True, exist_ok=True)
        command = [
            str(self._python()),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--upgrade",
            "--target",
            str(package_root),
            "--no-deps",
            *packages,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._runner_environment(),
            timeout=900,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-1200:] or result.stdout[-1200:])

    def _install_trainer_tokenizers(self) -> None:
        self._trainer_tokenizers.mkdir(parents=True, exist_ok=True)
        code = (
            "import sys;sys.path.insert(0,sys.argv[1]);"
            "from huggingface_hub import snapshot_download;"
            "snapshot_download(repo_id=sys.argv[2],revision=sys.argv[3],local_dir=sys.argv[4],"
            "allow_patterns=['vocab.json','merges.txt','tokenizer.json',"
            "'tokenizer_config.json','special_tokens_map.json'])"
        )
        environment = self._runner_environment()
        environment.update({"HF_HUB_OFFLINE": "0", "TRANSFORMERS_OFFLINE": "0"})
        for item in self._trainer_manifest.source["tokenizers"]:
            destination = self._trainer_tokenizers / str(item["cache_name"])
            result = subprocess.run(
                [
                    str(self._python()),
                    "-c",
                    code,
                    str(self._trainer_packages),
                    str(item["repository"]),
                    str(item["revision"]),
                    str(destination),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=600,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if result.returncode or not (destination / "vocab.json").is_file():
                raise RuntimeError(result.stderr[-1200:] or "Tokenizer download failed")

    def _install_trainer(self) -> None:
        item_id, manifest = "lora-training", self._trainer_manifest
        try:
            self._python()
            self._component_progress(item_id, 5, "Downloading pinned sd-scripts 0.10.5")
            archive = self.engine._download_verified(
                str(manifest.source["url"]),
                self.settings.engine_root
                / "downloads"
                / f"sd-scripts-{manifest.pinned_revision}.zip",
                int(manifest.source["bytes"]),
                str(manifest.source["sha256"]),
                lambda value: self._component_progress(
                    item_id, min(38, 5 + value // 3), "Downloading pinned sd-scripts 0.10.5"
                ),
            )
            extract_root = self.settings.trainer_runtime_root / "extract"
            shutil.rmtree(extract_root, ignore_errors=True)
            shutil.rmtree(self._trainer_source, ignore_errors=True)
            with zipfile.ZipFile(archive) as bundle:
                ensure_safe_archive_members(bundle.namelist())
                bundle.extractall(extract_root)
            candidates = [path for path in extract_root.iterdir() if path.is_dir()]
            if len(candidates) != 1:
                raise RuntimeError("The trainer archive layout is invalid")
            shutil.copytree(candidates[0], self._trainer_source)
            shutil.rmtree(extract_root, ignore_errors=True)
            self._component_progress(item_id, 42, "Installing isolated pinned trainer packages")
            self._install_packages(self._trainer_packages, list(manifest.source["packages"]))
            self._component_progress(item_id, 82, "Installing pinned offline SDXL tokenizers")
            self._install_trainer_tokenizers()
            self._component_progress(item_id, 94, "Verifying CUDA trainer imports")
            evidence = self._verify_trainer(write_state=False)
            (self.settings.trainer_runtime_root / "installation.json").write_text(
                json.dumps(
                    {
                        "revision": manifest.pinned_revision,
                        "archive_sha256": manifest.source["sha256"],
                        "packages": manifest.source["packages"],
                        "health": evidence,
                        "verified_at": utc_now(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            self._set_component(
                item_id, "ready", "Pinned sd-scripts trainer and CUDA imports are verified"
            )
        except Exception as error:
            logger.exception("managed trainer installation failed")
            self._set_component(item_id, "repair_needed", _safe_error(error))

    def _verify_trainer(self, *, write_state: bool = True) -> dict[str, Any]:
        command = [
            str(self._python()),
            str(self._training_runner),
            "--package-root",
            str(self._trainer_packages),
            "--source-root",
            str(self._trainer_source),
            "--tokenizer-root",
            str(self._trainer_tokenizers),
            "--self-test",
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._runner_environment(),
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode:
            if write_state:
                self._set_component("lora-training", "repair_needed", _safe_error(result.stderr))
            raise ValueError("The pinned local trainer failed its CUDA health check")
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        if not evidence.get("cuda"):
            raise ValueError("The local trainer could not access the NVIDIA GPU")
        if write_state:
            self._set_component(
                "lora-training", "ready", f"CUDA trainer verified on {evidence['gpu']}"
            )
        return evidence

    def _install_captioner(self) -> None:
        item_id, manifest = "captioning", self._caption_manifest
        try:
            self._python()
            root, source = self.settings.captioning_root, manifest.source
            for index, key in enumerate(("model", "tags")):
                asset = source[key]
                self._component_progress(
                    item_id, 5 + index * 70, f"Downloading verified caption {key}"
                )
                destination = root / str(asset["filename"])
                try:
                    self.engine._download_verified(
                        str(asset["url"]),
                        destination,
                        int(asset["bytes"]),
                        str(asset["sha256"]),
                        lambda value, index=index, key=key: self._component_progress(
                            item_id,
                            min(
                                78,
                                5 + index * 70 + round(value * (0.68 if index == 0 else 0.08)),
                            ),
                            f"Downloading verified caption {key}",
                        ),
                    )
                except RuntimeError:
                    self._download_huggingface_asset(
                        str(asset["url"]),
                        str(source["repository"]),
                        str(asset["filename"]),
                        manifest.pinned_revision,
                        destination,
                        int(asset["bytes"]),
                        str(asset["sha256"]),
                    )
                    self._component_progress(
                        item_id,
                        72 if index == 0 else 80,
                        f"Verified caption {key}",
                    )
            self._component_progress(item_id, 82, "Installing isolated ONNX Runtime")
            self._install_packages(self._caption_packages, list(source["packages"]))
            evidence = self._verify_captioner(write_state=False)
            (root / "installation.json").write_text(
                json.dumps(
                    {
                        "revision": manifest.pinned_revision,
                        "model_sha256": source["model"]["sha256"],
                        "tags_sha256": source["tags"]["sha256"],
                        "health": evidence,
                        "verified_at": utc_now(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            self._set_component(item_id, "ready", "Pinned local ONNX captioner is verified")
        except Exception as error:
            logger.exception("managed captioner installation failed")
            self._set_component(item_id, "repair_needed", _safe_error(error))

    def _download_huggingface_asset(
        self,
        url: str,
        repository: str,
        filename: str,
        revision: str,
        destination: Path,
        expected_size: int,
        expected_hash: str,
    ) -> None:
        lock = destination.parent / ".cache" / "huggingface" / "download" / f"{filename}.lock"
        lock.unlink(missing_ok=True)
        code = (
            "from huggingface_hub import hf_hub_download;import sys;"
            "print(hf_hub_download(repo_id=sys.argv[1],filename=sys.argv[2],"
            "revision=sys.argv[3],local_dir=sys.argv[4]))"
        )
        environment = self._runner_environment()
        environment.update(
            {
                "HF_HUB_OFFLINE": "0",
                "TRANSFORMERS_OFFLINE": "0",
                "HF_XET_HIGH_PERFORMANCE": "1",
                "HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY": "1",
            }
        )
        result = subprocess.run(
            [
                str(self._python()),
                "-c",
                code,
                repository,
                filename,
                revision,
                str(destination.parent),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=1800,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode:
            self._download_with_curl(url, destination)
        if (
            not destination.is_file()
            or destination.stat().st_size != expected_size
            or sha256_file(destination) != expected_hash
        ):
            destination.unlink(missing_ok=True)
            raise RuntimeError("The managed caption asset failed immutable verification")

    def _download_with_curl(self, url: str, destination: Path) -> None:
        if destination.exists() and destination.stat().st_size == 0:
            destination.unlink()
        command = [
            "curl.exe",
            "-L",
            "--fail",
            "--retry",
            "4",
            "--retry-all-errors",
            "--connect-timeout",
            "30",
        ]
        if destination.exists():
            command.extend(["-C", "-"])
        command.extend([url, "-o", str(destination)])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._runner_environment(),
            timeout=1800,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-1200:] or "Xet asset download failed")

    def _verify_captioner(self, *, write_state: bool = True) -> dict[str, Any]:
        source = self._caption_manifest.source
        model = self.settings.captioning_root / str(source["model"]["filename"])
        tags = self.settings.captioning_root / str(source["tags"]["filename"])
        for path, metadata in ((model, source["model"]), (tags, source["tags"])):
            if (
                not path.is_file()
                or path.stat().st_size != int(metadata["bytes"])
                or sha256_file(path) != metadata["sha256"]
            ):
                raise ValueError(f"The managed caption asset {path.name} failed verification")
        result = subprocess.run(
            [
                str(self._python()),
                str(self._caption_runner),
                "--package-root",
                str(self._caption_packages),
                "--model",
                str(model),
                "--tags",
                str(tags),
                "--self-test",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._runner_environment(),
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode:
            if write_state:
                self._set_component("captioning", "repair_needed", _safe_error(result.stderr))
            raise ValueError("The local caption model failed its health check")
        evidence = json.loads(result.stdout.strip().splitlines()[-1])
        if write_state:
            self._set_component(
                "captioning", "ready", "Local ONNX caption model and tag index verified"
            )
        return evidence

    def create_dataset(self, payload: TrainingDatasetInput) -> dict[str, Any]:
        if payload.character_id and not self.db.query_one(
            "SELECT id FROM characters WHERE id=?", (payload.character_id,)
        ):
            raise KeyError(payload.character_id)
        item_id, now = f"dataset-{uuid.uuid4().hex}", utc_now()
        self.db.execute(
            "INSERT INTO training_datasets(id,name,character_id,trigger_token,model_alias,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                item_id,
                payload.name,
                payload.character_id,
                payload.trigger_token,
                payload.model_alias,
                payload.notes,
                now,
                now,
            ),
        )
        (self.settings.training_dataset_root / item_id).mkdir(parents=True, exist_ok=True)
        return self.get_dataset(item_id)

    def list_datasets(self) -> list[dict[str, Any]]:
        return [
            self._inflate_dataset(row)
            for row in self.db.query_all("SELECT * FROM training_datasets ORDER BY updated_at DESC")
        ]

    def get_dataset(self, item_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM training_datasets WHERE id=?", (item_id,))
        if row is None:
            raise KeyError(item_id)
        return self._inflate_dataset(row)

    def _inflate_dataset(self, row: dict[str, Any]) -> dict[str, Any]:
        images = self.db.query_all(
            "SELECT * FROM training_images WHERE dataset_id=? ORDER BY created_at", (row["id"],)
        )
        for image in images:
            image["warnings"] = json.loads(image["warnings"])
        return {**row, "images": images, "image_count": len(images), "profiles": TRAINING_PROFILES}

    def import_images(self, dataset_id: str, source_paths: list[str]) -> dict[str, Any]:
        dataset = self.get_dataset(dataset_id)
        root = self.settings.training_dataset_root / dataset_id
        root.mkdir(parents=True, exist_ok=True)
        accepted, rejected = [], []
        existing = self.db.query_all(
            "SELECT sha256, perceptual_hash FROM training_images WHERE dataset_id=?", (dataset_id,)
        )
        for value in source_paths:
            source = Path(value)
            try:
                if not source.is_file() or source.suffix.lower() not in {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                }:
                    raise ValueError("Choose a PNG, JPEG, or WebP image")
                digest = sha256_file(source)
                if any(item["sha256"] == digest for item in existing):
                    raise ValueError("Exact duplicate already exists in this dataset")
                with Image.open(source) as opened:
                    opened.verify()
                with Image.open(source) as opened:
                    image = opened.convert("RGB")
                    width, height = image.size
                    perceptual = _average_hash(image)
                    blur = _blur_score(image)
                    warnings: list[str] = []
                    if min(width, height) < 512:
                        warnings.append("low_resolution")
                    if blur < 55:
                        warnings.append("possible_blur")
                    if any(
                        _hash_distance(perceptual, item["perceptual_hash"]) <= 4
                        for item in existing
                    ):
                        warnings.append("near_duplicate")
                    item_id, now = f"training-image-{uuid.uuid4().hex}", utc_now()
                    image_path = root / f"{item_id}.png"
                    thumbnail_path = root / f"{item_id}.thumb.jpg"
                    image.save(image_path, "PNG", optimize=True)
                    preview = image.copy()
                    preview.thumbnail((420, 420))
                    preview.save(thumbnail_path, "JPEG", quality=88, optimize=True)
                caption = dataset["trigger_token"]
                self.db.execute(
                    """INSERT INTO training_images(id,dataset_id,image_path,thumbnail_path,original_name,sha256,perceptual_hash,width,height,blur_score,face_count,caption,warnings,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item_id,
                        dataset_id,
                        str(image_path),
                        str(thumbnail_path),
                        source.name,
                        digest,
                        perceptual,
                        width,
                        height,
                        blur,
                        None,
                        caption,
                        json.dumps(warnings),
                        now,
                        now,
                    ),
                )
                existing.append({"sha256": digest, "perceptual_hash": perceptual})
                accepted.append(item_id)
            except Exception as error:
                rejected.append({"source_path": str(source), "warning": _safe_error(error)})
        if not accepted and rejected:
            raise ValueError(rejected[0]["warning"])
        return {"dataset": self.get_dataset(dataset_id), "accepted": accepted, "rejected": rejected}

    def update_caption(self, image_id: str, caption: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM training_images WHERE id=?", (image_id,))
        if row is None:
            raise KeyError(image_id)
        self.db.execute(
            "UPDATE training_images SET caption=?, updated_at=? WHERE id=?",
            (caption.strip(), utc_now(), image_id),
        )
        return self.db.query_one("SELECT * FROM training_images WHERE id=?", (image_id,)) or {}

    def caption_dataset(self, dataset_id: str) -> dict[str, Any]:
        component = self.db.query_one("SELECT state FROM engine_components WHERE id='captioning'")
        if not component or component["state"] != "ready":
            raise ValueError("Install and verify Local Captioning first")
        dataset = self.get_dataset(dataset_id)
        source = self._caption_manifest.source
        model = self.settings.captioning_root / str(source["model"]["filename"])
        tags = self.settings.captioning_root / str(source["tags"]["filename"])
        for image in dataset["images"]:
            result = subprocess.run(
                [
                    str(self._python()),
                    str(self._caption_runner),
                    "--package-root",
                    str(self._caption_packages),
                    "--model",
                    str(model),
                    "--tags",
                    str(tags),
                    "--image",
                    image["image_path"],
                    "--trigger-token",
                    dataset["trigger_token"],
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._runner_environment(),
                timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if result.returncode:
                raise ValueError("Local captioning failed; use Verify or Repair in Models & Engine")
            caption = json.loads(result.stdout.strip().splitlines()[-1])
            warnings = list(image["warnings"])
            face_count = caption.get("face_count")
            if face_count and face_count > 1 and "multiple_faces" not in warnings:
                warnings.append("multiple_faces")
            self.db.execute(
                "UPDATE training_images SET caption=?, face_count=?, warnings=?, updated_at=? WHERE id=?",
                (caption["caption"], face_count, json.dumps(warnings), utc_now(), image["id"]),
            )
        return self.get_dataset(dataset_id)

    def remove_image(self, image_id: str) -> None:
        row = self.db.query_one(
            "SELECT image_path,thumbnail_path FROM training_images WHERE id=?", (image_id,)
        )
        if row is None:
            raise KeyError(image_id)
        for value in (row["image_path"], row["thumbnail_path"]):
            path = Path(value)
            if (
                path.is_file()
                and self.settings.training_dataset_root.resolve() in path.resolve().parents
            ):
                path.unlink(missing_ok=True)
        self.db.execute("DELETE FROM training_images WHERE id=?", (image_id,))

    def dataset_media(self, image_id: str, variant: str) -> Path:
        field = (
            "image_path"
            if variant == "image"
            else "thumbnail_path"
            if variant == "thumbnail"
            else None
        )
        if field is None:
            raise KeyError(variant)
        row = self.db.query_one(f"SELECT {field} FROM training_images WHERE id=?", (image_id,))
        path = Path(row[field]) if row else Path()
        if not row or not path.is_file():
            raise KeyError(image_id)
        return path

    def estimates(self, dataset_id: str, profile: str, epochs: int) -> dict[str, Any]:
        dataset = self.get_dataset(dataset_id)
        config = TRAINING_PROFILES[profile]
        steps = max(
            1,
            math.ceil(
                len(dataset["images"])
                * config["repeats"]
                * epochs
                / config["gradient_accumulation"]
            ),
        )
        return {
            "profile": config["display_name"],
            "steps": steps,
            "seconds": steps * config["seconds_per_step"] + 240,
            "disk_gb": config["disk_gb"],
            "vram_gb": config["vram_gb"],
            "resolution": config["resolution"],
            "rank": config["rank"],
        }

    def start_run(self, payload: TrainingRunInput) -> dict[str, Any]:
        component = self.db.query_one(
            "SELECT state FROM engine_components WHERE id='lora-training'"
        )
        if not component or component["state"] != "ready":
            raise ValueError("Install and verify LoRA Training first")
        if self.db.query_one(
            "SELECT id FROM training_runs WHERE status IN ('queued','preparing','training','cancelling')"
        ):
            raise ValueError("Only one local training run can use the GPU at a time")
        dataset = self.get_dataset(payload.dataset_id)
        if not dataset["images"]:
            raise ValueError("Import at least one owned image before training")
        if any(not image["caption"].strip() for image in dataset["images"]):
            raise ValueError("Review every image caption before training")
        model = self.db.query_one(
            "SELECT * FROM model_packs WHERE alias=?", (dataset["model_alias"],)
        )
        if (
            not model
            or not model["installed"]
            or not model["verified"]
            or not Path(model.get("installed_path") or "").is_file()
        ):
            raise ValueError("The dataset's verified SDXL base model is unavailable")
        estimate = self.estimates(payload.dataset_id, payload.profile, payload.epochs)
        item_id, now = f"training-run-{uuid.uuid4().hex}", utc_now()
        output_name = (
            re.sub(r"[^A-Za-z0-9_-]+", "-", dataset["trigger_token"]).strip("-")
            + "-vanta-"
            + item_id[-8:]
        )
        output_dir = self.settings.training_run_root / item_id / "output"
        parameters = {
            "validation_prompt": payload.validation_prompt,
            **TRAINING_PROFILES[payload.profile],
        }
        self.db.execute(
            """INSERT INTO training_runs(id,dataset_id,character_id,profile,status,progress,current_epoch,total_epochs,current_step,total_steps,eta_seconds,model_alias,output_name,output_dir,parameters,estimates,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item_id,
                payload.dataset_id,
                dataset["character_id"],
                payload.profile,
                "queued",
                0,
                0,
                payload.epochs,
                0,
                estimate["steps"],
                estimate["seconds"],
                dataset["model_alias"],
                output_name,
                str(output_dir),
                json.dumps(parameters),
                json.dumps(estimate),
                now,
                now,
            ),
        )
        thread = threading.Thread(target=self._execute_run, args=(item_id, None), daemon=True)
        self._run_threads[item_id] = thread
        thread.start()
        return self.get_run(item_id)

    def _write_run_inputs(self, run: dict[str, Any], dataset: dict[str, Any]) -> tuple[Path, Path]:
        root = self.settings.training_run_root / run["id"]
        image_root = root / "dataset"
        image_root.mkdir(parents=True, exist_ok=True)
        for image in dataset["images"]:
            target = image_root / f"{image['id']}.png"
            if not target.exists():
                shutil.copy2(image["image_path"], target)
            target.with_suffix(".txt").write_text(image["caption"].strip(), encoding="utf-8")
        config = TRAINING_PROFILES[run["profile"]]
        image_dir = str(image_root).replace("\\", "\\\\")
        config_path = root / "dataset.toml"
        config_path.write_text(
            "\n".join(
                [
                    "[general]",
                    'caption_extension = ".txt"',
                    "shuffle_caption = false",
                    "keep_tokens = 1",
                    "",
                    "[[datasets]]",
                    f"resolution = {config['resolution']}",
                    "batch_size = 1",
                    "enable_bucket = true",
                    "bucket_no_upscale = true",
                    "min_bucket_reso = 256",
                    f"max_bucket_reso = {config['resolution']}",
                    "bucket_reso_steps = 64",
                    "",
                    "[[datasets.subsets]]",
                    f'image_dir = "{image_dir}"',
                    f"num_repeats = {config['repeats']}",
                ]
            ),
            encoding="utf-8",
        )
        prompt = (
            run["parameters"].get("validation_prompt")
            or f"portrait photograph of {dataset['trigger_token']}, neutral editorial lighting"
        )
        sample_path = root / "validation-prompts.txt"
        sample_path.write_text(
            f"{prompt} --w {config['resolution']} --h {config['resolution']} --d 42 --s 8 --l 7.0",
            encoding="utf-8",
        )
        return config_path, sample_path

    def _execute_run(self, run_id: str, resume_path: str | None) -> None:
        process: subprocess.Popen[str] | None = None
        log_path = self.settings.training_run_root / run_id / "training.log"
        try:
            run = self.get_run(run_id)
            dataset = self.get_dataset(run["dataset_id"])
            model = self.db.query_one(
                "SELECT installed_path FROM model_packs WHERE alias=?", (run["model_alias"],)
            )
            if not model:
                raise ValueError("The selected base model is unavailable")
            config_path, sample_path = self._write_run_inputs(run, dataset)
            output_dir = Path(run["output_dir"])
            output_dir.mkdir(parents=True, exist_ok=True)
            self.db.execute(
                "UPDATE training_runs SET status='preparing',progress=2,started_at=COALESCE(started_at,?),updated_at=? WHERE id=?",
                (utc_now(), utc_now(), run_id),
            )
            profile = TRAINING_PROFILES[run["profile"]]
            arguments = [
                f"--pretrained_model_name_or_path={model['installed_path']}",
                f"--dataset_config={config_path}",
                f"--output_dir={output_dir}",
                f"--output_name={run['output_name']}",
                "--save_model_as=safetensors",
                f"--max_train_epochs={run['total_epochs']}",
                "--save_every_n_epochs=1",
                "--save_state",
                "--save_state_on_train_end",
                "--network_module=networks.lora",
                f"--network_dim={profile['rank']}",
                f"--network_alpha={profile['alpha']}",
                "--network_train_unet_only",
                "--learning_rate=0.0001",
                "--optimizer_type=Adafactor",
                "--optimizer_args",
                "relative_step=False",
                "scale_parameter=False",
                "warmup_init=False",
                "--lr_scheduler=constant",
                "--mixed_precision=bf16",
                "--save_precision=bf16",
                "--gradient_checkpointing",
                f"--gradient_accumulation_steps={profile['gradient_accumulation']}",
                "--cache_latents",
                "--cache_latents_to_disk",
                "--cache_text_encoder_outputs",
                "--cache_text_encoder_outputs_to_disk",
                "--max_data_loader_n_workers=0",
                "--sdpa",
                "--seed=42",
                "--console_log_simple",
                f"--sample_prompts={sample_path}",
                "--sample_every_n_epochs=1",
                "--sample_sampler=euler_a",
                f"--tokenizer_cache_dir={self._trainer_tokenizers}",
            ]
            if resume_path:
                arguments.append(f"--resume={resume_path}")
            command = [
                str(self._python()),
                str(self._training_runner),
                "--package-root",
                str(self._trainer_packages),
                "--source-root",
                str(self._trainer_source),
                "--tokenizer-root",
                str(self._trainer_tokenizers),
                "--",
                *arguments,
            ]
            self.db.execute(
                "UPDATE training_runs SET status='training',progress=4,error_message=NULL,cancellation_requested=0,updated_at=? WHERE id=?",
                (utc_now(), run_id),
            )
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._runner_environment(),
                cwd=self._trainer_source,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                bufsize=1,
            )
            with self._lock:
                self._processes[run_id] = process
            tail: list[str] = []
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log:
                assert process.stdout is not None
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    tail = (tail + [line.strip()])[-18:]
                    progress_match = re.search(r"steps:\s*\d+%.*?(\d+)/(\d+)", line)
                    epoch_match = re.search(r"epoch\s+(\d+)/(\d+)", line, re.IGNORECASE)
                    updates: list[Any] = [utc_now(), run_id]
                    if progress_match:
                        step, total = map(int, progress_match.groups())
                        progress = min(96, 5 + round(90 * step / max(1, total)))
                        eta = max(0, round((total - step) * profile["seconds_per_step"]))
                        self.db.execute(
                            "UPDATE training_runs SET current_step=?,total_steps=?,progress=?,eta_seconds=?,updated_at=? WHERE id=?",
                            (step, total, progress, eta, *updates),
                        )
                    if epoch_match:
                        self.db.execute(
                            "UPDATE training_runs SET current_epoch=?,updated_at=? WHERE id=?",
                            (int(epoch_match.group(1)), *updates),
                        )
                    state = self.db.query_one(
                        "SELECT cancellation_requested FROM training_runs WHERE id=?", (run_id,)
                    )
                    if state and state["cancellation_requested"]:
                        process.terminate()
                        break
            exit_code = process.wait(timeout=90)
            self._scan_checkpoints(run_id)
            resume = self._latest_resume_state(run_id)
            if self.db.query_one(
                "SELECT cancellation_requested FROM training_runs WHERE id=?", (run_id,)
            )["cancellation_requested"]:
                self.db.execute(
                    "UPDATE training_runs SET status='cancelled',resume_state_path=?,eta_seconds=NULL,updated_at=? WHERE id=?",
                    (resume, utc_now(), run_id),
                )
            elif exit_code:
                raise RuntimeError(" ".join(tail[-8:]) or f"Trainer exited with code {exit_code}")
            else:
                self.db.execute(
                    "UPDATE training_runs SET status='completed',progress=100,current_epoch=total_epochs,current_step=total_steps,eta_seconds=0,resume_state_path=?,completed_at=?,updated_at=? WHERE id=?",
                    (resume, utc_now(), utc_now(), run_id),
                )
        except Exception as error:
            logger.exception("training run failed")
            self._scan_checkpoints(run_id)
            self.db.execute(
                "UPDATE training_runs SET status='failed',error_message=?,resume_state_path=?,eta_seconds=NULL,updated_at=? WHERE id=?",
                (_safe_error(error), self._latest_resume_state(run_id), utc_now(), run_id),
            )
        finally:
            with self._lock:
                self._processes.pop(run_id, None)

    def _latest_resume_state(self, run_id: str) -> str | None:
        root = self.settings.training_run_root / run_id / "output"
        states = sorted(
            (path for path in root.glob("*-state") if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return str(states[0]) if states else None

    def _scan_checkpoints(self, run_id: str) -> None:
        run = self.db.query_one("SELECT * FROM training_runs WHERE id=?", (run_id,))
        if not run:
            return
        output = Path(run["output_dir"])
        samples = sorted(output.rglob("*.png")) if output.exists() else []
        for path in sorted(output.glob("*.safetensors")):
            epoch_suffix = re.search(r"-(\d{6})$", path.stem)
            epoch = int(epoch_suffix.group(1)) if epoch_suffix else int(run["total_epochs"])
            sample = next(
                (
                    candidate
                    for candidate in reversed(samples)
                    if f"e{epoch:06d}" in candidate.name or f"-{epoch:06d}" in candidate.name
                ),
                samples[-1] if samples else None,
            )
            existing = self.db.query_one(
                "SELECT id FROM training_checkpoints WHERE file_path=?", (str(path),)
            )
            if existing:
                self.db.execute(
                    "UPDATE training_checkpoints SET epoch=?,step=?,validation_sample_path=? WHERE id=?",
                    (
                        epoch,
                        int(run["total_steps"]),
                        str(sample) if sample else None,
                        existing["id"],
                    ),
                )
                continue
            checkpoint_id = f"checkpoint-{uuid.uuid4().hex}"
            self.db.execute(
                "INSERT INTO training_checkpoints(id,run_id,epoch,step,file_path,sha256,file_size,validation_sample_path,selected,created_at) VALUES(?,?,?,?,?,?,?,?,0,?)",
                (
                    checkpoint_id,
                    run_id,
                    epoch,
                    int(run["total_steps"]),
                    str(path),
                    sha256_file(path),
                    path.stat().st_size,
                    str(sample) if sample else None,
                    utc_now(),
                ),
            )
        selected = self.db.query_one(
            "SELECT id FROM training_checkpoints WHERE run_id=? AND selected=1", (run_id,)
        )
        if not selected:
            latest = self.db.query_one(
                "SELECT id FROM training_checkpoints WHERE run_id=? ORDER BY epoch DESC,created_at DESC LIMIT 1",
                (run_id,),
            )
            if latest:
                self.db.execute(
                    "UPDATE training_checkpoints SET selected=1 WHERE id=?", (latest["id"],)
                )
                self.db.execute(
                    "UPDATE training_runs SET selected_checkpoint_id=? WHERE id=?",
                    (latest["id"], run_id),
                )

    def list_runs(self) -> list[dict[str, Any]]:
        return [
            self._inflate_run(row)
            for row in self.db.query_all("SELECT * FROM training_runs ORDER BY created_at DESC")
        ]

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM training_runs WHERE id=?", (run_id,))
        if row is None:
            raise KeyError(run_id)
        return self._inflate_run(row)

    def _inflate_run(self, row: dict[str, Any]) -> dict[str, Any]:
        row["parameters"] = json.loads(row["parameters"])
        row["estimates"] = json.loads(row["estimates"])
        row["cancellation_requested"] = bool(row["cancellation_requested"])
        row["checkpoints"] = self.db.query_all(
            "SELECT * FROM training_checkpoints WHERE run_id=? ORDER BY epoch,created_at",
            (row["id"],),
        )
        for checkpoint in row["checkpoints"]:
            checkpoint["selected"] = bool(checkpoint["selected"])
        return row

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] not in ACTIVE_STATES:
            return run
        self.db.execute(
            "UPDATE training_runs SET status='cancelling',cancellation_requested=1,updated_at=? WHERE id=?",
            (utc_now(), run_id),
        )
        with self._lock:
            process = self._processes.get(run_id)
            if process and process.poll() is None:
                process.terminate()
        return self.get_run(run_id)

    def resume_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if (
            run["status"] not in {"failed", "cancelled"}
            or not run.get("resume_state_path")
            or not Path(run["resume_state_path"]).is_dir()
        ):
            raise ValueError("No saved trainer state is available for this run")
        if self.db.query_one(
            "SELECT id FROM training_runs WHERE status IN ('queued','preparing','training','cancelling')"
        ):
            raise ValueError("Another training run is active")
        self.db.execute(
            "UPDATE training_runs SET status='queued',error_message=NULL,cancellation_requested=0,updated_at=? WHERE id=?",
            (utc_now(), run_id),
        )
        thread = threading.Thread(
            target=self._execute_run, args=(run_id, run["resume_state_path"]), daemon=True
        )
        self._run_threads[run_id] = thread
        thread.start()
        return self.get_run(run_id)

    def select_checkpoint(self, run_id: str, checkpoint_id: str) -> dict[str, Any]:
        checkpoint = self.db.query_one(
            "SELECT * FROM training_checkpoints WHERE id=? AND run_id=?", (checkpoint_id, run_id)
        )
        if checkpoint is None:
            raise KeyError(checkpoint_id)
        self.db.execute(
            "UPDATE training_checkpoints SET selected=CASE WHEN id=? THEN 1 ELSE 0 END WHERE run_id=?",
            (checkpoint_id, run_id),
        )
        self.db.execute(
            "UPDATE training_runs SET selected_checkpoint_id=?,updated_at=? WHERE id=?",
            (checkpoint_id, utc_now(), run_id),
        )
        return self.get_run(run_id)

    def install_checkpoint(self, run_id: str, payload: TrainingInstallInput) -> dict[str, Any]:
        self._scan_checkpoints(run_id)
        run = self.get_run(run_id)
        checkpoint = self.db.query_one(
            "SELECT * FROM training_checkpoints WHERE id=? AND run_id=?",
            (payload.checkpoint_id, run_id),
        )
        if checkpoint is None or not Path(checkpoint["file_path"]).is_file():
            raise KeyError(payload.checkpoint_id)
        dataset = self.get_dataset(run["dataset_id"])
        imported = self.loras.import_lora(
            LoraImportInput(
                source_path=checkpoint["file_path"],
                name=payload.name,
                source_notes=f"Trained locally by Vanta run {run_id} with sd-scripts 0.10.5",
                license_notes="User-owned training dataset; base-model license remains applicable.",
                trigger_token=dataset["trigger_token"],
                default_strength=payload.strength,
                default_clip_strength=1.0,
            )
        )
        self.loras.assign(
            payload.character_id,
            CharacterLoraInput(
                lora_id=imported["id"],
                position=99,
                strength=payload.strength,
                clip_strength=1.0,
                enabled=True,
            ),
        )
        self.select_checkpoint(run_id, payload.checkpoint_id)
        self.db.execute(
            "UPDATE training_runs SET installed_lora_id=?,updated_at=? WHERE id=?",
            (imported["id"], utc_now(), run_id),
        )
        return {"run": self.get_run(run_id), "lora": imported}

    def validation_media(self, checkpoint_id: str) -> Path:
        row = self.db.query_one(
            "SELECT validation_sample_path FROM training_checkpoints WHERE id=?", (checkpoint_id,)
        )
        path = Path(row.get("validation_sample_path") or "") if row else Path()
        if not row or not path.is_file():
            raise KeyError(checkpoint_id)
        return path

    def _recover(self) -> None:
        rows = self.db.query_all(
            "SELECT id FROM training_runs WHERE status IN ('queued','preparing','training','cancelling')"
        )
        for row in rows:
            resume = self._latest_resume_state(row["id"])
            self.db.execute(
                "UPDATE training_runs SET status='failed',error_message=?,resume_state_path=?,updated_at=? WHERE id=?",
                (
                    "The desktop closed during training. Resume from the last saved epoch when available.",
                    resume,
                    utc_now(),
                    row["id"],
                ),
            )

    def close(self) -> None:
        with self._lock:
            for process in self._processes.values():
                if process.poll() is None:
                    process.terminate()
