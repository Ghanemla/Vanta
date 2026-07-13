from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .comfy_runtime import ManagedComfyRuntime, sha256_file, validate_safetensors
from .config import Settings
from .database import Database, utc_now

logger = logging.getLogger("vanta.orchestrator.engine")

ComponentState = Literal[
    "not_installed",
    "installing",
    "verifying",
    "stopped",
    "starting",
    "restarting",
    "ready",
    "update_available",
    "repair_needed",
    "crashed",
    "unsupported",
]


class LicenseMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    source_url: str | None = None
    redistribution_reviewed: bool | None = None
    acceptance_required: bool | None = None


class ComponentManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    display_name: str
    version: str
    pinned_revision: str
    source: dict[str, Any]
    license: LicenseMetadata
    install_strategy: Literal["managed_archive", "managed_fixture"]
    health_checks: list[dict[str, str]]
    repair_strategy: Literal["reverify_and_restore"]
    dependencies: list[str]
    provided_capabilities: list[str]


class CoreManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(ge=1)
    manifest_id: str
    components: list[ComponentManifest]


class HardwareRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_vram_gb: int
    recommended_vram_gb: int
    minimum_ram_gb: int


class ModelPackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    alias: str
    display_name: str
    model_family: str
    capabilities: list[str]
    hardware: HardwareRecommendation
    disk_gb: float
    download: dict[str, Any]
    license: LicenseMetadata
    sha256: str
    target_path: str
    compatible_workflows: list[str]
    active_default: bool


class ModelPackCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(ge=1)
    packs: list[ModelPackManifest]


@dataclass(slots=True)
class GenerationResult:
    job_id: str
    generation_id: str


def detect_hardware(data_dir: Path) -> dict[str, Any]:
    """Detect local values; production never advertises a fixture hardware profile."""
    gpu_name, vram_gb = "Unknown GPU", 0
    try:
        import subprocess

        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            timeout=5,
        )
        first = result.stdout.strip().splitlines()[0]
        gpu_name, memory = (part.strip() for part in first.rsplit(",", maxsplit=1))
        vram_gb = max(0, round(int(memory) / 1024))
    except (FileNotFoundError, IndexError, OSError, ValueError):
        pass
    ram_gb = 0
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            ram_gb = round(status.total_phys / 1024**3)
    else:
        ram_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3)
    free_disk_gb = round(shutil.disk_usage(data_dir).free / 1024**3)
    return {
        "gpu_name": gpu_name,
        "vram_gb": vram_gb,
        "ram_gb": ram_gb,
        "free_disk_gb": free_disk_gb,
    }


class WorkflowCompiler:
    version = "image-sdxl-photoreal-v1"

    @staticmethod
    def compile_prompt(request: dict[str, Any]) -> str:
        order = (
            "character_identity",
            "wardrobe",
            "expression",
            "pose",
            "location",
            "lighting",
            "camera",
            "quality",
            "direction",
            "custom_tags",
        )
        parts: list[str] = []
        for key in order:
            value = request.get(key, "")
            if isinstance(value, list):
                value = ", ".join(str(item).strip() for item in value if str(item).strip())
            if str(value).strip():
                parts.append(str(value).strip())
        return ", ".join(parts)

    def compile(
        self,
        request: dict[str, Any],
        checkpoint_name: str,
        loras: list[dict[str, Any]] | None = None,
        source_image_name: str | None = None,
        identity_image_name: str | None = None,
    ) -> dict[str, Any]:
        positive = self.compile_prompt(request)
        if not positive:
            raise ValueError("Describe the frame before generating")
        negative = str(request.get("negative_prompt", "")).strip()
        workflow = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint_name}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["1", 1]}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
            "4": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": request["width"], "height": request["height"], "batch_size": 1},
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": request["seed"],
                    "steps": request["steps"],
                    "cfg": request["guidance"],
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0],
                },
            },
            "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "Vanta/generation", "images": ["6", 0]},
            },
        }
        if loras:
            model_source: list[Any] = ["1", 0]
            clip_source: list[Any] = ["1", 1]
            for index, lora in enumerate(loras, start=8):
                workflow[str(index)] = {
                    "class_type": "LoraLoader",
                    "inputs": {
                        "model": model_source,
                        "clip": clip_source,
                        "lora_name": lora["filename"],
                        "strength_model": lora["strength"],
                        "strength_clip": lora["clip_strength"],
                    },
                }
                model_source, clip_source = [str(index), 0], [str(index), 1]
            workflow["2"]["inputs"]["clip"] = clip_source
            workflow["3"]["inputs"]["clip"] = clip_source
            workflow["5"]["inputs"]["model"] = model_source
        if source_image_name:
            workflow["9"] = {"class_type": "LoadImage", "inputs": {"image": source_image_name}}
            workflow["10"] = {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["9", 0], "vae": ["1", 2]},
            }
            workflow["5"]["inputs"]["latent_image"] = ["10", 0]
            workflow["5"]["inputs"]["denoise"] = request["variation_strength"]
        if identity_image_name:
            workflow["20"] = {"class_type": "LoadImage", "inputs": {"image": identity_image_name}}
            workflow["21"] = {
                "class_type": "IPAdapterUnifiedLoader",
                "inputs": {
                    "model": workflow["5"]["inputs"]["model"],
                    "preset": "PLUS FACE (portraits)",
                },
            }
            workflow["22"] = {
                "class_type": "IPAdapterAdvanced",
                "inputs": {
                    "model": ["21", 0],
                    "ipadapter": ["21", 1],
                    "image": ["20", 0],
                    "weight": 0.6,
                    "weight_type": "linear",
                    "combine_embeds": "concat",
                    "start_at": 0.0,
                    "end_at": 1.0,
                    "embeds_scaling": "V only",
                },
            }
            workflow["5"]["inputs"]["model"] = ["22", 0]
        return workflow

    def diagnostic(self, checkpoint_name: str) -> dict[str, Any]:
        return self.compile(
            {
                "character_identity": "original adult portrait",
                "direction": "neutral diagnostic image",
                "negative_prompt": "",
                "seed": 7,
                "width": 512,
                "height": 512,
                "steps": 1,
                "guidance": 1.0,
            },
            checkpoint_name,
        )

    @staticmethod
    def upscale(source_image_name: str, model_name: str) -> dict[str, Any]:
        """Compile a non-generative derivative workflow through ComfyUI's native tiled model node."""
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": source_image_name}},
            "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model_name}},
            "3": {
                "class_type": "ImageUpscaleWithModel",
                "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]},
            },
            "4": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "Vanta/upscale", "images": ["3", 0]},
            },
        }


class EngineService:
    allowed_component_actions = {"install", "repair", "cancel", "health_check", "start", "stop"}
    allowed_pack_actions = {"verify", "repair", "remove", "set_default"}

    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.core = CoreManifest.model_validate_json(
            (settings.engine_manifest_dir / "core-components.v1.json").read_text(encoding="utf-8")
        )
        self.pack_collection = ModelPackCollection.model_validate_json(
            (settings.engine_manifest_dir / "model-packs.v1.json").read_text(encoding="utf-8")
        )
        runtime_component = next(
            item for item in self.core.components if item.id == "workflow-runtime"
        )
        self.runtime = ManagedComfyRuntime(
            settings, runtime_component.source, runtime_component.pinned_revision
        )
        self.hardware = detect_hardware(settings.data_dir)
        self._install_thread: threading.Thread | None = None
        self._seed()

    def close(self) -> None:
        self.runtime.stop()

    def _seed(self) -> None:
        now = utc_now()
        for component in self.core.components:
            state = "not_installed" if component.id == "workflow-runtime" else "unsupported"
            message = (
                "Install the managed local image engine to continue"
                if component.id == "workflow-runtime"
                else "Coming later; this capability is not included in the current image release"
            )
            self.db.execute(
                """INSERT OR IGNORE INTO engine_components
                (id, display_name, manifest_version, state, progress, last_health_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (component.id, component.display_name, component.version, state, 0, message, now),
            )
        for pack in self.pack_collection.packs:
            self.db.execute(
                """INSERT OR IGNORE INTO model_packs
                (id, alias, display_name, state, installed, verified, is_default, progress, metadata, updated_at)
                VALUES (?, ?, ?, 'not_installed', 0, 0, 0, 0, ?, ?)""",
                (pack.id, pack.alias, pack.display_name, pack.model_dump_json(), now),
            )
        self._sync_runtime()

    def _sync_runtime(self) -> None:
        snapshot = self.runtime.snapshot()
        self.db.execute(
            "UPDATE engine_components SET state=?, progress=?, last_health_message=?, updated_at=? WHERE id='workflow-runtime'",
            (
                snapshot.state,
                100 if snapshot.state == "ready" else 0,
                snapshot.message,
                utc_now(),
            ),
        )

    def _set_component_progress(self, progress: int, message: str) -> None:
        self.db.execute(
            "UPDATE engine_components SET state='installing', progress=?, last_health_message=?, updated_at=? WHERE id='workflow-runtime'",
            (progress, message, utc_now()),
        )

    def _install_runtime(self) -> None:
        try:
            self.runtime.install(self._set_component_progress)
        except Exception as error:
            logger.exception("managed ComfyUI installation failed")
            self.runtime.mark_repair_needed(str(error))
            self.db.execute(
                "UPDATE engine_components SET state='repair_needed', progress=0, last_health_message=?, updated_at=? WHERE id='workflow-runtime'",
                (str(error), utc_now()),
            )
        finally:
            self._sync_runtime()

    def list_components(self) -> list[dict[str, Any]]:
        self._sync_runtime()
        manifests = {item.id: item for item in self.core.components}
        return [
            {
                **row,
                "capabilities": manifests[row["id"]].provided_capabilities,
                "dependencies": manifests[row["id"]].dependencies,
            }
            for row in self.db.query_all("SELECT * FROM engine_components ORDER BY display_name")
        ]

    def component_action(self, item_id: str, action: str) -> dict[str, Any]:
        if action not in self.allowed_component_actions:
            raise ValueError("Unsupported component action")
        if item_id != "workflow-runtime":
            raise ValueError(
                "This capability is coming later and is not available in the image release"
            )
        if action in {"install", "repair"}:
            if self._install_thread is None or not self._install_thread.is_alive():
                self._install_thread = threading.Thread(target=self._install_runtime, daemon=True)
                self._install_thread.start()
        elif action == "cancel":
            self.runtime.cancel_install()
        elif action == "start":
            self.runtime.start()
            self.runtime.wait_healthy()
        elif action == "stop":
            self.runtime.stop()
        else:
            self.runtime.start()
            self.runtime.wait_healthy(timeout=15)
        self._sync_runtime()
        return self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,)) or {}

    def _pack_row(self, alias: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM model_packs WHERE alias=?", (alias,))
        if row is None:
            raise KeyError(alias)
        return row

    def import_model(self, source_path: str, alias: str, license_notes: str = "") -> dict[str, Any]:
        if alias != "photoreal_balanced":
            raise ValueError("The first release supports import into photoreal_balanced only")
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("Choose an existing local .safetensors checkpoint")
        validate_safetensors(source)
        self.settings.model_root.mkdir(parents=True, exist_ok=True)
        destination = self.settings.model_root / source.name
        if destination.resolve() != source:
            shutil.copy2(source, destination)
        actual_hash = sha256_file(destination)
        metadata = json.loads(self._pack_row(alias)["metadata"])
        metadata.update(
            {
                "filename": destination.name,
                "model_family": "SDXL-compatible (pending engine verification)",
                "source_information": "Imported from a user-selected local file",
                "sha256": actual_hash,
            }
        )
        self.db.execute(
            """UPDATE model_packs SET state='verifying', installed=1, verified=0, progress=65,
            metadata=?, installed_path=?, original_path=?, file_size=?, license_notes=?, imported_at=?, updated_at=?
            WHERE alias=?""",
            (
                json.dumps(metadata),
                str(destination),
                str(source),
                destination.stat().st_size,
                license_notes,
                utc_now(),
                utc_now(),
                alias,
            ),
        )
        return self.verify_model(alias)

    def import_upscaler(
        self, source_path: str, alias: str, license_notes: str = ""
    ) -> dict[str, Any]:
        expected = {
            "realesrgan_x2plus": "RealESRGAN_x2plus.pth",
            "ultrasharp_x4": "4xUltrasharp_4xUltrasharpV10.pt",
        }
        if alias not in expected:
            raise ValueError("Choose a supported Vanta upscale pack")
        source = Path(source_path).expanduser().resolve()
        if source.suffix.lower() not in {".pth", ".pt"} or not source.is_file():
            raise ValueError("Choose an existing .pth or .pt local upscale model")
        if source.stat().st_size < 1_000_000:
            raise ValueError("This upscale model file is too small to be valid")
        self.settings.upscale_root.mkdir(parents=True, exist_ok=True)
        destination = self.settings.upscale_root / expected[alias]
        if source != destination:
            shutil.copy2(source, destination)
        metadata = json.loads(self._pack_row(alias)["metadata"])
        metadata.update(
            {
                "filename": destination.name,
                "sha256": sha256_file(destination),
                "source_information": "Imported from a user-selected local file",
            }
        )
        self.db.execute(
            """UPDATE model_packs SET state='ready', installed=1, verified=1, progress=100,
            metadata=?, installed_path=?, original_path=?, file_size=?, license_notes=?, imported_at=?, updated_at=?
            WHERE alias=?""",
            (
                json.dumps(metadata),
                str(destination),
                str(source),
                destination.stat().st_size,
                license_notes,
                utc_now(),
                utc_now(),
                alias,
            ),
        )
        return self._pack_row(alias)

    def import_identity_adapter(
        self, adapter_source_path: str, clip_vision_source_path: str, license_notes: str = ""
    ) -> dict[str, Any]:
        adapter_source = Path(adapter_source_path).expanduser().resolve()
        clip_source = Path(clip_vision_source_path).expanduser().resolve()
        for source, label in (
            (adapter_source, "identity adapter"),
            (clip_source, "CLIP Vision encoder"),
        ):
            if not source.is_file():
                raise ValueError(f"Choose an existing local {label} file")
            validate_safetensors(source)
        adapter_target = (
            self.settings.ipadapter_root / "ip-adapter-plus-face_sdxl_vit-h.safetensors"
        )
        clip_target = self.settings.clip_vision_root / "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
        self.settings.ipadapter_root.mkdir(parents=True, exist_ok=True)
        self.settings.clip_vision_root.mkdir(parents=True, exist_ok=True)
        if adapter_source != adapter_target:
            shutil.copy2(adapter_source, adapter_target)
        if clip_source != clip_target:
            shutil.copy2(clip_source, clip_target)
        metadata = json.loads(self._pack_row("identity_plus_face_sdxl")["metadata"])
        metadata.update(
            {
                "filename": adapter_target.name,
                "sha256": sha256_file(adapter_target),
                "clip_vision_filename": clip_target.name,
                "clip_vision_sha256": sha256_file(clip_target),
                "source_information": "Imported from user-selected local adapter and CLIP Vision files",
            }
        )
        self.db.execute(
            """UPDATE model_packs SET state='ready', installed=1, verified=1, progress=100,
            metadata=?, installed_path=?, original_path=?, file_size=?, license_notes=?, imported_at=?, updated_at=?
            WHERE alias='identity_plus_face_sdxl'""",
            (
                json.dumps(metadata),
                str(adapter_target),
                str(adapter_source),
                adapter_target.stat().st_size + clip_target.stat().st_size,
                license_notes,
                utc_now(),
                utc_now(),
            ),
        )
        return self.verify_identity_adapter()

    def verify_identity_adapter(self) -> dict[str, Any]:
        row = self._pack_row("identity_plus_face_sdxl")
        adapter = self.settings.ipadapter_root / "ip-adapter-plus-face_sdxl_vit-h.safetensors"
        encoder = self.settings.clip_vision_root / "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"
        if not adapter.is_file() or not encoder.is_file():
            raise ValueError(
                "Identity Lock is missing its adapter or CLIP Vision encoder; import both to repair"
            )
        validate_safetensors(adapter)
        validate_safetensors(encoder)
        self.runtime.start()
        if not self.runtime.wait_healthy(timeout=45):
            raise ValueError("Start the Local Generation Engine before verifying Identity Lock")
        nodes = self.runtime._request_json("/object_info")
        if not {"IPAdapterUnifiedLoader", "IPAdapterAdvanced"}.issubset(nodes):
            raise ValueError("Vanta's compatible identity adapter runtime is not installed")
        metadata = json.loads(row["metadata"])
        metadata.update(
            {
                "filename": adapter.name,
                "sha256": sha256_file(adapter),
                "clip_vision_filename": encoder.name,
                "clip_vision_sha256": sha256_file(encoder),
                "verification": "adapter and encoder validated; compatible local runtime nodes available",
            }
        )
        self.db.execute(
            "UPDATE model_packs SET state='ready', installed=1, verified=1, progress=100, metadata=?, updated_at=? WHERE alias='identity_plus_face_sdxl'",
            (json.dumps(metadata), utc_now()),
        )
        return self._pack_row("identity_plus_face_sdxl")

    def verify_upscaler(self, alias: str) -> dict[str, Any]:
        names = {
            "realesrgan_x2plus": "RealESRGAN_x2plus.pth",
            "ultrasharp_x4": "4xUltrasharp_4xUltrasharpV10.pt",
        }
        filename = names[alias]
        path = self.settings.upscale_root / filename
        if not path.is_file() or path.stat().st_size < 1_000_000:
            raise ValueError(
                "The selected local upscale model is missing; import it again to repair the pack"
            )
        self.runtime.start()
        if not self.runtime.wait_healthy(timeout=45):
            raise ValueError("Start the Local Generation Engine before verifying this upscale pack")
        model_names = (
            self.runtime._request_json("/object_info")
            .get("UpscaleModelLoader", {})
            .get("input", {})
            .get("required", {})
            .get("model_name", [[]])[0]
        )
        if filename not in model_names:
            raise ValueError(
                "The Local Generation Engine cannot see this upscale model; repair the pack"
            )
        metadata = json.loads(self._pack_row(alias)["metadata"])
        metadata.update({"filename": filename, "sha256": sha256_file(path)})
        self.db.execute(
            "UPDATE model_packs SET state='ready', installed=1, verified=1, progress=100, metadata=?, updated_at=? WHERE alias=?",
            (json.dumps(metadata), utc_now(), alias),
        )
        return self._pack_row(alias)

    def verify_model(self, alias: str) -> dict[str, Any]:
        row = self._pack_row(alias)
        path = Path(row.get("installed_path") or "")
        if not path.is_file():
            self.db.execute(
                "UPDATE model_packs SET state='repair_needed', installed=0, verified=0, progress=0, updated_at=? WHERE alias=?",
                (utc_now(), alias),
            )
            raise ValueError(
                "The selected model file is missing; import it again to repair the pack"
            )
        self.runtime.start()
        if not self.runtime.wait_healthy(timeout=45):
            raise ValueError("Start the Local Generation Engine before verifying this model")
        actual_hash = sha256_file(path)
        self.db.execute(
            "UPDATE model_packs SET state='verifying', progress=80, updated_at=? WHERE alias=?",
            (utc_now(), alias),
        )
        try:
            self.runtime.submit(WorkflowCompiler().diagnostic(path.name), lambda _value, _max: None)
        except Exception as error:
            self.db.execute(
                "UPDATE model_packs SET state='repair_needed', verified=0, progress=0, updated_at=? WHERE alias=?",
                (utc_now(), alias),
            )
            raise ValueError(
                "The local image engine could not load this SDXL checkpoint"
            ) from error
        metadata = json.loads(row["metadata"])
        metadata.update({"filename": path.name, "sha256": actual_hash, "model_family": "SDXL"})
        self.db.execute(
            "UPDATE model_packs SET state='ready', installed=1, verified=1, progress=100, metadata=?, updated_at=? WHERE alias=?",
            (json.dumps(metadata), utc_now(), alias),
        )
        if not any(
            item["is_default"] for item in self.db.query_all("SELECT is_default FROM model_packs")
        ):
            self.db.execute("UPDATE model_packs SET is_default=1 WHERE alias=?", (alias,))
        return self._pack_row(alias)

    def list_packs(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.db.query_all("SELECT * FROM model_packs ORDER BY display_name"):
            metadata = json.loads(row.pop("metadata"))
            installed_path = Path(row.get("installed_path") or "")
            if row["installed"] and not installed_path.is_file():
                self.db.execute(
                    "UPDATE model_packs SET state='repair_needed', installed=0, verified=0, progress=0, updated_at=? WHERE id=?",
                    (utc_now(), row["id"]),
                )
                row.update({"state": "repair_needed", "installed": 0, "verified": 0, "progress": 0})
            rows.append(
                {
                    **row,
                    **metadata,
                    "recommended": metadata["alias"] == "photoreal_balanced"
                    and self.hardware["vram_gb"] >= metadata["hardware"]["recommended_vram_gb"],
                    "installed": bool(row["installed"]),
                    "verified": bool(row["verified"]),
                    "is_default": bool(row["is_default"]),
                }
            )
        return rows

    def pack_action(self, item_id: str, action: str) -> dict[str, Any]:
        if action not in self.allowed_pack_actions:
            raise ValueError("Unsupported model-pack action")
        row = self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,))
        if row is None:
            raise KeyError(item_id)
        if action in {"verify", "repair"}:
            if row["alias"] == "identity_plus_face_sdxl":
                return self.verify_identity_adapter()
            if row["alias"] in {"realesrgan_x2plus", "ultrasharp_x4"}:
                return self.verify_upscaler(row["alias"])
            return self.verify_model(row["alias"])
        if action == "remove":
            if row["is_default"]:
                raise ValueError("Choose another verified model before removing the default")
            Path(row.get("installed_path") or "").unlink(missing_ok=True)
            self.db.execute(
                "UPDATE model_packs SET state='not_installed', installed=0, verified=0, progress=0, installed_path=NULL, updated_at=? WHERE id=?",
                (utc_now(), item_id),
            )
        elif action == "set_default":
            if not row["installed"] or not row["verified"]:
                raise ValueError("Only a verified local model can be the default")
            self.db.execute("UPDATE model_packs SET is_default=0, updated_at=?", (utc_now(),))
            self.db.execute(
                "UPDATE model_packs SET is_default=1, updated_at=? WHERE id=?", (utc_now(), item_id)
            )
        return self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,)) or {}

    def model_for_alias(self, alias: str) -> dict[str, Any]:
        row = self._pack_row(alias)
        if not row["installed"] or not row["verified"]:
            raise ValueError("Import and verify a compatible local model before generating")
        if not Path(row.get("installed_path") or "").is_file():
            raise ValueError("The selected model file is missing; repair it before generating")
        return row

    def diagnostics(self) -> dict[str, Any]:
        snapshot = self.runtime.snapshot()
        active = self.db.query_one(
            "SELECT id, status, progress FROM generation_jobs WHERE status NOT IN ('completed', 'failed', 'cancelled') ORDER BY created_at DESC LIMIT 1"
        )
        model = self.db.query_one(
            "SELECT alias, installed_path, verified, metadata FROM model_packs WHERE is_default=1"
        )
        model_metadata = json.loads(model["metadata"]) if model else {}
        return {
            "summary": snapshot.message,
            "messages": [
                "Orchestrator is bound to 127.0.0.1",
                f"ComfyUI {snapshot.revision}: {snapshot.state}",
                f"GPU: {self.hardware['gpu_name']} ({self.hardware['vram_gb']} GB VRAM)",
                "No cloud services are configured",
            ],
            "raw_logs": [],
            "runtime": {
                "engine_path": str(self.settings.runtime_root),
                "engine_port": snapshot.port,
                "model_storage_path": str(self.settings.model_root),
                "active_model_alias": model["alias"] if model else None,
                "active_model_file": model["installed_path"] if model else None,
                "model_sha256": model_metadata.get("sha256"),
                "model_verified": bool(model and model["verified"]),
                "gpu": self.hardware["gpu_name"],
                "vram_gb": self.hardware["vram_gb"],
                "ram_gb": self.hardware["ram_gb"],
                "free_disk_gb": self.hardware["free_disk_gb"],
                "current_job": active,
                "workflow_version": WorkflowCompiler.version,
                "output_path": str(self.settings.media_root),
            },
        }


class GenerationService:
    def __init__(self, db: Database, engine: EngineService):
        self.db = db
        self.engine = engine
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def recover(self) -> None:
        self.db.execute(
            "UPDATE generation_jobs SET status='failed', error_message='Vanta closed before this generation finished', updated_at=? WHERE status IN ('checking_engine', 'preparing', 'loading_model', 'generating', 'decoding', 'saving', 'cancelling')",
            (utc_now(),),
        )
        if self.db.query_one("SELECT id FROM generation_jobs WHERE status='queued' LIMIT 1"):
            self._start_worker()

    def queue(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id, now = f"job-{uuid.uuid4().hex}", utc_now()
        self.db.execute(
            "INSERT INTO generation_jobs(id, status, request_json, progress, created_at, updated_at) VALUES (?, 'queued', ?, 0, ?, ?)",
            (job_id, json.dumps(request), now, now),
        )
        self._start_worker()
        return self.get(job_id)

    def list(self, limit: int = 40) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM generation_jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [self._present(row) for row in rows]

    def retry(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] not in {"failed", "cancelled"}:
            raise ValueError("Only failed or cancelled jobs can be retried")
        return self.queue(json.loads(job["request_json"]))

    def _start_worker(self) -> None:
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, daemon=True)
                self._worker.start()

    def _update(
        self, job_id: str, status: str, progress: int, error: str | None = None, **extra: Any
    ) -> None:
        fields = ["status=?", "progress=?", "error_message=?", "updated_at=?"]
        values: list[Any] = [status, progress, error, utc_now()]
        for key, value in extra.items():
            fields.append(f"{key}=?")
            values.append(value)
        values.append(job_id)
        self.db.execute(f"UPDATE generation_jobs SET {', '.join(fields)} WHERE id=?", tuple(values))

    def _work(self) -> None:
        while True:
            job = self.db.query_one(
                "SELECT * FROM generation_jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
            )
            if job is None:
                return
            self._run(job)

    def _run(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        request = json.loads(job["request_json"])
        started = time.monotonic()
        try:
            self._update(job_id, "checking_engine", 5, started_at=utc_now())
            self.engine.runtime.start()
            if not self.engine.runtime.wait_healthy(timeout=45):
                raise RuntimeError("The Local Generation Engine is not ready")
            if request.get("operation") == "upscale":
                self._run_upscale(job_id, request, started)
                return
            self._update(job_id, "preparing", 10)
            model = self.engine.model_for_alias(request["model_alias"])
            self._update(job_id, "loading_model", 15)
            loras = self._resolve_loras(request)
            source_image_name = self._prepare_variation_source(request)
            identity_image_name = self._prepare_identity_reference(request)
            workflow = WorkflowCompiler().compile(
                request,
                Path(model["installed_path"]).name,
                loras,
                source_image_name,
                identity_image_name,
            )

            def progress(value: int, maximum: int) -> None:
                percentage = min(88, 20 + round(68 * value / maximum))
                self._update(
                    job_id,
                    "generating",
                    percentage,
                    current_step=value,
                    total_steps=maximum,
                )

            prompt_id, history = self.engine.runtime.submit(workflow, progress)
            self._update(job_id, "decoding", 90, prompt_id=prompt_id)
            output = history.get("outputs", {}).get("7", {}).get("images", [])
            if not output:
                raise RuntimeError("The image engine completed without an output image")
            image = output[0]
            source = (
                self.engine.runtime.root / "output" / image.get("subfolder", "") / image["filename"]
            )
            if not source.is_file():
                raise RuntimeError("The image engine output file is missing")
            self._update(job_id, "saving", 95)
            generation_id = f"generation-{uuid.uuid4().hex}"
            destination = self.engine.settings.media_root / f"{generation_id}.png"
            thumbnail = self.engine.settings.media_root / f"{generation_id}.thumb.jpg"
            shutil.copy2(source, destination)
            with Image.open(destination) as rendered:
                rendered.thumbnail((480, 480))
                rendered.convert("RGB").save(thumbnail, "JPEG", quality=88, optimize=True)
            metadata = {
                "workflow_version": WorkflowCompiler.version,
                "compiled_positive_prompt": WorkflowCompiler.compile_prompt(request),
                "negative_prompt": request.get("negative_prompt", ""),
                "model_filename": Path(model["installed_path"]).name,
                "model_sha256": json.loads(model["metadata"]).get("sha256"),
                "comfyui_revision": self.engine.runtime.revision,
                "steps": request["steps"],
                "guidance": request["guidance"],
                "loras": loras,
                "source_generation_id": request.get("source_generation_id"),
                "variation_strength": request.get("variation_strength"),
                "identity_reference_id": request.get("identity_reference_id"),
                "disclosure": True,
                "duration_seconds": round(time.monotonic() - started, 2),
                "request": request,
            }
            self.db.execute(
                """INSERT INTO generations(id, character_id, recipe_id, image_path, thumbnail_path, prompt, negative_prompt, seed, model_alias, width, height, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    generation_id,
                    request.get("character_id"),
                    request.get("recipe_id"),
                    str(destination),
                    str(thumbnail),
                    metadata["compiled_positive_prompt"],
                    metadata["negative_prompt"],
                    request["seed"],
                    request["model_alias"],
                    request["width"],
                    request["height"],
                    json.dumps(metadata),
                    utc_now(),
                ),
            )
            self._update(job_id, "completed", 100, completed_at=utc_now())
        except Exception as error:
            current = self.get(job_id)
            if current["status"] == "cancelling":
                self._update(
                    job_id,
                    "cancelled",
                    current["progress"],
                    "Generation cancelled",
                    completed_at=utc_now(),
                )
            else:
                logger.exception("generation job failed id=%s", job_id)
                self._update(
                    job_id, "failed", current["progress"], str(error), completed_at=utc_now()
                )

    def _run_upscale(self, job_id: str, request: dict[str, Any], started: float) -> None:
        source_id = str(request.get("source_generation_id") or "")
        source_row = self.db.query_one(
            "SELECT image_path FROM generations WHERE id=?", (source_id,)
        )
        if source_row is None or not Path(source_row["image_path"]).is_file():
            raise ValueError("The selected source image is no longer available for upscaling")
        profile = request.get("upscale_profile") or "realesrgan_x2plus"
        models = {
            "realesrgan_x2plus": ("RealESRGAN_x2plus.pth", 2),
            "ultrasharp_x4": ("4xUltrasharp_4xUltrasharpV10.pt", 4),
        }
        if profile not in models:
            raise ValueError("Choose a supported local upscale profile")
        model_name, scale = models[profile]
        model_path = self.engine.settings.upscale_root / model_name
        if not model_path.is_file():
            raise ValueError(
                f"{model_name} is not installed. Import the local model pack in Models & Engine first."
            )
        layout = self.engine.runtime.installed_layout()
        if layout is None:
            raise RuntimeError("The Local Generation Engine is not installed")
        input_dir = layout[0].parent / "input" / "Vanta"
        input_dir.mkdir(parents=True, exist_ok=True)
        source_name = f"{source_id}-upscale-source.png"
        shutil.copy2(source_row["image_path"], input_dir / source_name)
        self._update(job_id, "preparing", 15)
        workflow = WorkflowCompiler.upscale(f"Vanta/{source_name}", model_name)

        def progress(value: int, maximum: int) -> None:
            percentage = min(90, 20 + round(70 * value / max(maximum, 1)))
            self._update(job_id, "generating", percentage, current_step=value, total_steps=maximum)

        prompt_id, history = self.engine.runtime.submit(workflow, progress)
        self._update(job_id, "saving", 94, prompt_id=prompt_id)
        output = history.get("outputs", {}).get("4", {}).get("images", [])
        if not output:
            raise RuntimeError("The local upscaler completed without an output image")
        image = output[0]
        rendered = (
            self.engine.runtime.root / "output" / image.get("subfolder", "") / image["filename"]
        )
        if not rendered.is_file():
            raise RuntimeError("The local upscaler output file is missing")
        generation_id = f"generation-{uuid.uuid4().hex}"
        destination = self.engine.settings.media_root / f"{generation_id}.png"
        thumbnail = self.engine.settings.media_root / f"{generation_id}.thumb.jpg"
        shutil.copy2(rendered, destination)
        with Image.open(destination) as final_image:
            width, height = final_image.size
            final_image.thumbnail((480, 480))
            final_image.convert("RGB").save(thumbnail, "JPEG", quality=88, optimize=True)
        source_metadata = self.db.query_one(
            "SELECT metadata FROM generations WHERE id=?", (source_id,)
        )
        metadata = {
            "workflow_version": "image-upscale-realesrgan-v1",
            "operation": "upscale",
            "derivative_of": source_id,
            "upscale_profile": profile,
            "upscale_model": model_name,
            "upscale_model_sha256": sha256_file(model_path),
            "scale": scale,
            "tiled_execution": True,
            "source_metadata_available": bool(source_metadata),
            "disclosure": True,
            "duration_seconds": round(time.monotonic() - started, 2),
            "request": request,
        }
        self.db.execute(
            """INSERT INTO generations(id, character_id, recipe_id, image_path, thumbnail_path, prompt, negative_prompt, seed, model_alias, width, height, metadata, created_at)
            VALUES (?, NULL, NULL, ?, ?, ?, '', 0, 'photoreal_balanced', ?, ?, ?, ?)""",
            (
                generation_id,
                str(destination),
                str(thumbnail),
                f"{scale}x upscale of {source_id}",
                width,
                height,
                json.dumps(metadata),
                utc_now(),
            ),
        )
        self._update(job_id, "completed", 100, completed_at=utc_now())

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        self._update(job_id, "cancelling", job["progress"])
        self.engine.runtime.interrupt(job.get("prompt_id"))
        return self.get(job_id)

    def _resolve_loras(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        requested = list(request.get("lora_ids", []))
        if not requested and request.get("character_id"):
            requested = [
                row["lora_id"]
                for row in self.db.query_all(
                    "SELECT lora_id FROM character_loras WHERE character_id=? AND enabled=1 ORDER BY position",
                    (request["character_id"],),
                )
            ]
        if len(requested) > 8:
            raise ValueError("Use no more than eight compatible LoRAs in one generation")
        resolved: list[dict[str, Any]] = []
        for lora_id in requested:
            row = self.db.query_one("SELECT * FROM lora_packs WHERE id=?", (lora_id,))
            if row is None or not row["enabled"]:
                raise ValueError("One selected LoRA is no longer available")
            if row["model_family"] != "SDXL":
                raise ValueError("The selected LoRA is not compatible with the SDXL workflow")
            if not Path(row["installed_path"]).is_file():
                raise ValueError("A selected LoRA file is missing; repair or remove it")
            assignment = self.db.query_one(
                "SELECT strength, clip_strength FROM character_loras WHERE character_id=? AND lora_id=?",
                (request.get("character_id"), lora_id),
            )
            resolved.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "filename": row["filename"],
                    "sha256": row["sha256"],
                    "strength": float(
                        assignment["strength"] if assignment else row["default_strength"]
                    ),
                    "clip_strength": float(
                        assignment["clip_strength"] if assignment else row["default_clip_strength"]
                    ),
                }
            )
        return resolved

    def _prepare_variation_source(self, request: dict[str, Any]) -> str | None:
        generation_id = request.get("source_generation_id")
        if not generation_id:
            return None
        source = self.db.query_one(
            "SELECT image_path FROM generations WHERE id=?", (generation_id,)
        )
        if source is None or not Path(source["image_path"]).is_file():
            raise ValueError("The selected source image is no longer available for a variation")
        layout = self.engine.runtime.installed_layout()
        if layout is None:
            raise ValueError("Start the Local Generation Engine before creating a variation")
        input_dir = layout[0].parent / "input" / "Vanta"
        input_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{generation_id}.png"
        shutil.copy2(source["image_path"], input_dir / filename)
        return f"Vanta/{filename}"

    def _prepare_identity_reference(self, request: dict[str, Any]) -> str | None:
        reference_id = request.get("identity_reference_id")
        if not reference_id and request.get("character_id"):
            reference = self.db.query_one(
                "SELECT id FROM character_references WHERE character_id=? AND is_primary=1",
                (request["character_id"],),
            )
            reference_id = reference["id"] if reference else None
        if not reference_id:
            return None
        request["identity_reference_id"] = reference_id
        pack = self.engine._pack_row("identity_plus_face_sdxl")
        if not pack["installed"] or not pack["verified"]:
            raise ValueError(
                "Install and verify the Identity — Plus Face SDXL pack before using a reference"
            )
        reference = self.db.query_one(
            "SELECT image_path FROM character_references WHERE id=?", (reference_id,)
        )
        if reference is None or not Path(reference["image_path"]).is_file():
            raise ValueError("The selected identity reference is no longer available")
        layout = self.engine.runtime.installed_layout()
        if layout is None:
            raise ValueError("Start the Local Generation Engine before using an identity reference")
        input_dir = layout[0].parent / "input" / "Vanta"
        input_dir.mkdir(parents=True, exist_ok=True)
        filename = f"identity-{reference_id}.png"
        shutil.copy2(reference["image_path"], input_dir / filename)
        return f"Vanta/{filename}"

    def get(self, job_id: str) -> dict[str, Any]:
        job = self.db.query_one("SELECT * FROM generation_jobs WHERE id=?", (job_id,))
        if job is None:
            raise KeyError(job_id)
        return self._present(job)

    def _present(self, job: dict[str, Any]) -> dict[str, Any]:
        result = dict(job)
        if result["status"] == "queued":
            result["queue_position"] = (
                int(
                    self.db.query_one(
                        "SELECT COUNT(*) AS count FROM generation_jobs WHERE status='queued' AND created_at < ?",
                        (result["created_at"],),
                    )["count"]
                )
                + 1
            )
        else:
            result["queue_position"] = None
        return result
