from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import os
import platform
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from urllib.error import URLError
from urllib.request import Request, urlopen

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .comfy_runtime import (
    ManagedComfyRuntime,
    ensure_safe_archive_members,
    sha256_file,
    validate_safetensors,
)
from .config import Settings
from .database import Database, utc_now
from .installation_jobs import InstallationJobs
from .video import (
    VIDEO_FFMPEG_BYTES,
    VIDEO_FFMPEG_FILENAME,
    VIDEO_FFMPEG_SHA256,
    VIDEO_MODEL_ALIAS,
    VIDEO_MODEL_FILENAME,
    VIDEO_PROFILES,
    VIDEO_TEXT_ENCODER_FILENAME,
    LtxVideoWorkflowCompiler,
    encode_mp4,
    extract_last_frame,
    is_owned_path,
)

logger = logging.getLogger("vanta.orchestrator.engine")

POSE_PACK_ALIAS = "pose_xinsir_sdxl"
POSE_CONTROL_FILENAME = "xinsir-openpose-sdxl-1.0.safetensors"
POSE_EXTENSION_PATCH = "dwpose-minimal-imports-003"
IDENTITY_PACK_ALIAS = "identity_plus_face_sdxl"
IDENTITY_ADAPTER_FILENAME = "ip-adapter-plus-face_sdxl_vit-h.safetensors"
IDENTITY_CLIP_FILENAME = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"

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
    install_strategy: Literal[
        "managed_archive",
        "managed_fixture",
        "managed_extension_archive",
        "managed_python_runtime",
        "managed_model",
        "bundled_runtime",
    ]
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

    @classmethod
    def workflow_version(
        cls,
        *,
        source_image: bool = False,
        identity_image: bool = False,
        pose_image: bool = False,
    ) -> str:
        if identity_image and pose_image:
            return "image-sdxl-identity-pose-v1"
        if identity_image:
            return "image-sdxl-identity-ipadapter-v1"
        if pose_image:
            return "image-sdxl-pose-controlnet-v1"
        if source_image:
            return "image-sdxl-variation-img2img-v1"
        return cls.version

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
            "variation_prompt",
            "custom_tags",
        )
        parts: list[str] = []
        for key in order:
            value = request.get(key, "")
            if isinstance(value, list):
                value = ", ".join(str(item).strip() for item in value if str(item).strip())
            text = str(value).strip()
            if text and text not in parts:
                parts.append(text)
        return ", ".join(parts)

    def compile(
        self,
        request: dict[str, Any],
        checkpoint_name: str,
        loras: list[dict[str, Any]] | None = None,
        source_image_name: str | None = None,
        identity_image_name: str | None = None,
        pose_image_name: str | None = None,
        pose_strength: float | None = None,
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
                    "sampler_name": request.get("sampler", "euler"),
                    "scheduler": request.get("scheduler", "normal"),
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
                    "weight": request.get("identity_strength", 0.6),
                    "weight_type": "linear",
                    "combine_embeds": "concat",
                    "start_at": 0.0,
                    "end_at": 1.0,
                    "embeds_scaling": "V only",
                },
            }
            workflow["5"]["inputs"]["model"] = ["22", 0]
        if pose_image_name:
            workflow["30"] = {"class_type": "LoadImage", "inputs": {"image": pose_image_name}}
            workflow["31"] = {
                "class_type": "DiffControlNetLoader",
                "inputs": {
                    "model": workflow["5"]["inputs"]["model"],
                    "control_net_name": POSE_CONTROL_FILENAME,
                },
            }
            workflow["32"] = {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "control_net": ["31", 0],
                    "image": ["30", 0],
                    "strength": pose_strength if pose_strength is not None else 0.8,
                    "start_percent": 0.0,
                    "end_percent": 1.0,
                },
            }
            workflow["5"]["inputs"]["positive"] = ["32", 0]
            workflow["5"]["inputs"]["negative"] = ["32", 1]
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

    def compile_inpaint(
        self,
        request: dict[str, Any],
        checkpoint_name: str,
        source_image_name: str,
        mask_image_name: str,
        loras: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        positive = str(request.get("region_prompt") or "").strip()
        if not positive:
            raise ValueError("Describe the change inside the painted region")
        negative = str(request.get("region_negative_prompt") or "").strip()
        workflow: dict[str, Any] = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": checkpoint_name},
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": positive, "clip": ["1", 1]},
            },
            "3": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": ["1", 1]},
            },
            "20": {"class_type": "LoadImage", "inputs": {"image": source_image_name}},
            "21": {
                "class_type": "LoadImageMask",
                "inputs": {"image": mask_image_name, "channel": "red"},
            },
            "4": {
                "class_type": "VAEEncodeForInpaint",
                "inputs": {
                    "pixels": ["20", 0],
                    "vae": ["1", 2],
                    "mask": ["21", 0],
                    "grow_mask_by": 12,
                },
            },
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": request["seed"],
                    "steps": request["steps"],
                    "cfg": request["guidance"],
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": request["inpaint_strength"],
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0],
                },
            },
            "6": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
            },
            "22": {
                "class_type": "ImageCompositeMasked",
                "inputs": {
                    "destination": ["20", 0],
                    "source": ["6", 0],
                    "x": 0,
                    "y": 0,
                    "resize_source": False,
                    "mask": ["21", 0],
                },
            },
            "7": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "Vanta/inpaint", "images": ["22", 0]},
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
        return workflow

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


class FluxWorkflowCompiler:
    """Compile FLUX graphs without leaking engine node details into the desktop UI."""

    version = "image-flux-photoreal-v1"

    def compile(
        self,
        request: dict[str, Any],
        checkpoint_name: str,
        loras: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        positive = WorkflowCompiler.compile_prompt(request)
        if not positive:
            raise ValueError("Describe the frame before generating")
        negative = str(request.get("negative_prompt", "")).strip()
        workflow: dict[str, Any] = {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": checkpoint_name},
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": positive, "clip": ["1", 1]},
            },
            "3": {
                "class_type": "FluxGuidance",
                "inputs": {"guidance": request["guidance"], "conditioning": ["2", 0]},
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": ["1", 1]},
            },
            "5": {
                "class_type": "EmptySD3LatentImage",
                "inputs": {
                    "width": request["width"],
                    "height": request["height"],
                    "batch_size": 1,
                },
            },
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": request["seed"],
                    "steps": request["steps"],
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "denoise": 1.0,
                    "model": ["1", 0],
                    "positive": ["3", 0],
                    "negative": ["4", 0],
                    "latent_image": ["5", 0],
                },
            },
            "7": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["6", 0], "vae": ["1", 2]},
            },
            "8": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "Vanta/flux-generation", "images": ["7", 0]},
            },
        }
        if loras:
            model_source: list[Any] = ["1", 0]
            clip_source: list[Any] = ["1", 1]
            for index, lora in enumerate(loras, start=20):
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
            workflow["4"]["inputs"]["clip"] = clip_source
            workflow["6"]["inputs"]["model"] = model_source
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
                "guidance": 3.5,
            },
            checkpoint_name,
        )


def checkpoint_family(header: dict[str, Any]) -> str:
    keys = [key.lower() for key in header if key != "__metadata__"]
    joined = " ".join(keys)
    if (
        "model.diffusion_model.double_blocks" in joined
        and any(key.startswith("text_encoders.t5xxl") for key in keys)
        and any(key.startswith("vae.") for key in keys)
    ):
        return "FLUX"
    return "SDXL"


class EngineService:
    allowed_component_actions = {
        "install",
        "repair",
        "cancel",
        "health_check",
        "start",
        "stop",
        "remove",
        "verify",
        "pause",
        "resume",
        "restart",
        "update",
    }
    allowed_pack_actions = {"install", "verify", "repair", "remove", "set_default"}

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
        self.installation_jobs = InstallationJobs(db)
        self.installation_jobs.recover()
        self.hardware = detect_hardware(settings.data_dir)
        self._install_thread: threading.Thread | None = None
        self._identity_install_thread: threading.Thread | None = None
        self._pack_threads: dict[str, threading.Thread] = {}
        self._seed()
        self._sync_pose_component()
        self._sync_identity_component()
        self._sync_image_finishing_component()
        self._sync_video_components()

    def close(self) -> None:
        self.runtime.stop()

    def _seed(self) -> None:
        now = utc_now()
        for component in self.core.components:
            state = (
                "not_installed"
                if component.id
                in {
                    "workflow-runtime",
                    "pose-control",
                    "identity-lock",
                    "image-finishing",
                    "video-generation",
                    "reference-motion",
                    "lora-training",
                    "captioning",
                }
                else "unsupported"
            )
            message = (
                "Install the managed local image engine to continue"
                if component.id == "workflow-runtime"
                else "Install the reviewed local DWPose preprocessor"
                if component.id == "pose-control"
                else "Install the reviewed local identity-conditioning extension"
                if component.id == "identity-lock"
                else "Install the managed Image Workflow Engine to enable local editing"
                if component.id == "image-finishing"
                else "Install and verify the optional LTX-Video model pack"
                if component.id == "video-generation"
                else "Install Pose Control and Image-to-Video to extract broad motion"
                if component.id == "reference-motion"
                else "Install the pinned local sd-scripts trainer"
                if component.id == "lora-training"
                else "Install the pinned local image captioner"
                if component.id == "captioning"
                else "Coming later; this capability is not included in the current image release"
            )
            self.db.execute(
                """INSERT OR IGNORE INTO engine_components
                (id, display_name, manifest_version, state, progress, last_health_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (component.id, component.display_name, component.version, state, 0, message, now),
            )
            self.db.execute(
                "UPDATE engine_components SET display_name=?, manifest_version=?, updated_at=? WHERE id=?",
                (component.display_name, component.version, now, component.id),
            )
        for pack in self.pack_collection.packs:
            self.db.execute(
                """INSERT OR IGNORE INTO model_packs
                (id, alias, display_name, state, installed, verified, is_default, progress, metadata, updated_at)
                VALUES (?, ?, ?, 'not_installed', 0, 0, 0, 0, ?, ?)""",
                (pack.id, pack.alias, pack.display_name, pack.model_dump_json(), now),
            )
            self.db.execute(
                "UPDATE model_packs SET display_name=?, metadata=?, updated_at=? WHERE id=? AND installed=0",
                (pack.display_name, pack.model_dump_json(), now, pack.id),
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

    def _set_component_progress(
        self,
        progress: int,
        message: str,
        stage: str = "installing",
        downloaded_bytes: int = 0,
        total_bytes: int = 0,
        job_id: str | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE engine_components SET state='installing', progress=?, last_health_message=?, updated_at=? WHERE id='workflow-runtime'",
            (progress, message, utc_now()),
        )
        if job_id:
            self.installation_jobs.update(
                job_id,
                stage,
                message,
                message,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes or None,
                percentage=progress,
                resumable=True,
            )

    def _install_runtime(self, job_id: str) -> None:
        try:
            self.runtime.install(
                lambda progress, message, stage, downloaded, total: self._set_component_progress(
                    progress, message, stage, downloaded, total, job_id
                )
            )
            self.installation_jobs.update(
                job_id, "ready", "Ready", "Local engine is verified and ready", percentage=100
            )
        except Exception as error:
            cancelled = "cancelled" in str(error).lower()
            if cancelled:
                self.runtime.mark_repair_needed(
                    "Installation cancelled; the partial download is preserved"
                )
                self.installation_jobs.update(
                    job_id,
                    "cancelled",
                    "Cancelled",
                    "Installation cancelled; retry resumes when the source supports it.",
                    cancellation_requested=True,
                )
            else:
                logger.exception("managed ComfyUI installation failed")
                self.runtime.mark_repair_needed(str(error))
                self.installation_jobs.update(
                    job_id,
                    "failed",
                    "Failed",
                    "Vanta could not install the local engine. Review diagnostics and retry.",
                    error_category="installation_failed",
                    technical_details=str(error),
                )
            self.db.execute(
                "UPDATE engine_components SET state='repair_needed', progress=0, last_health_message=?, updated_at=? WHERE id='workflow-runtime'",
                (str(error), utc_now()),
            )
        finally:
            self._sync_runtime()

    def list_components(self) -> list[dict[str, Any]]:
        self._sync_runtime()
        self._sync_pose_component()
        self._sync_identity_component()
        self._sync_image_finishing_component()
        self._sync_video_components()
        manifests = {item.id: item for item in self.core.components}
        return [
            {
                **row,
                "capabilities": manifests[row["id"]].provided_capabilities,
                "dependencies": manifests[row["id"]].dependencies,
                "version": manifests[row["id"]].version,
                "revision": manifests[row["id"]].pinned_revision,
                "source": manifests[row["id"]].source.get("url"),
                "sha256": manifests[row["id"]].source.get("sha256"),
                "license": manifests[row["id"]].license.model_dump(),
                "installation_job": self.installation_jobs.current(row["id"]),
            }
            for row in self.db.query_all("SELECT * FROM engine_components ORDER BY display_name")
        ]

    def component_action(self, item_id: str, action: str) -> dict[str, Any]:
        if action not in self.allowed_component_actions:
            raise ValueError("Unsupported component action")
        if action == "verify":
            action = "health_check"
        elif action == "update":
            manifest = next((item for item in self.core.components if item.id == item_id), None)
            row = self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,))
            if manifest is None or row is None:
                raise KeyError(item_id)
            if row["manifest_version"] == manifest.version:
                self.db.execute(
                    "UPDATE engine_components SET last_health_message=?,updated_at=? WHERE id=?",
                    (f"Already current at {manifest.version}", utc_now(), item_id),
                )
                return (
                    self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,))
                    or {}
                )
            action = "repair"
        elif action == "restart":
            self.runtime.stop()
            self.runtime.start()
            if not self.runtime.wait_healthy(timeout=45):
                raise ValueError("The local image engine did not restart")
            action = "health_check"
        if item_id == "pose-control":
            if action in {"install", "repair"}:
                if self._install_thread is None or not self._install_thread.is_alive():
                    self._component_progress(1, "Preparing the managed DWPose installation")
                    self._install_thread = threading.Thread(
                        target=self._install_pose_component, daemon=True
                    )
                    self._install_thread.start()
            elif action == "remove":
                self._remove_pose_component()
            elif action == "health_check":
                self._verify_pose_component()
            else:
                raise ValueError("Use Install, Verify, Repair, or Remove for Pose Control")
            return self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,)) or {}
        if item_id == "identity-lock":
            if action in {"install", "repair"}:
                if (
                    self._identity_install_thread is None
                    or not self._identity_install_thread.is_alive()
                ):
                    self._identity_component_progress(
                        1, "Preparing the managed Identity Lock installation"
                    )
                    self._identity_install_thread = threading.Thread(
                        target=self._install_identity_component, daemon=True
                    )
                    self._identity_install_thread.start()
            elif action == "remove":
                self._remove_identity_component()
            elif action == "health_check":
                self._verify_identity_component()
            else:
                raise ValueError("Use Install, Verify, Repair, or Remove for Identity Lock")
            return self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,)) or {}
        if item_id == "image-finishing":
            if action in {"install", "repair", "health_check", "start"}:
                self._verify_image_finishing_component()
            elif action == "stop":
                self.runtime.stop()
                self._sync_image_finishing_component()
            elif action == "remove":
                raise ValueError(
                    "Image Editing is part of the shared local workflow runtime and cannot be removed separately"
                )
            else:
                raise ValueError("Use Install, Verify, Repair, Start, or Stop for Image Editing")
            return self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,)) or {}
        if item_id in {"video-generation", "reference-motion"}:
            pack = self._pack_row(VIDEO_MODEL_ALIAS)
            if action in {"install", "repair"} and not (pack["installed"] and pack["verified"]):
                self.pack_action(pack["id"], action)
                self.db.execute(
                    "UPDATE engine_components SET state='installing', progress=1, last_health_message=?, updated_at=? WHERE id=?",
                    (
                        "Downloading and verifying the optional local LTX-Video model pack",
                        utc_now(),
                        item_id,
                    ),
                )
            elif action in {"install", "repair", "health_check", "start"}:
                self._verify_video_components()
            elif action == "stop":
                self.runtime.stop()
                self._sync_video_components()
            elif action == "remove":
                raise ValueError(
                    "Remove the optional LTX-Video model pack; shared local runtime nodes remain installed"
                )
            else:
                raise ValueError("Use Install, Verify, Repair, Start, or Stop for local video")
            return self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,)) or {}
        if item_id != "workflow-runtime":
            raise ValueError(
                "This capability is coming later and is not available in the image release"
            )
        if action in {"install", "repair"}:
            if self._install_thread is None or not self._install_thread.is_alive():
                source = str(self.runtime.source.get("url", ""))
                expected_bytes = int(self.runtime.source.get("bytes", 0))
                job_id = self.installation_jobs.start(
                    "workflow-runtime",
                    action,
                    source=source,
                    destination=self.runtime.archive_path,
                    total_bytes=expected_bytes,
                    resumable=True,
                )
                self._set_component_progress(
                    0, "Checking storage before download", "checking_storage", job_id=job_id
                )
                self._install_thread = threading.Thread(
                    target=self._install_runtime, args=(job_id,), daemon=True
                )
                self._install_thread.start()
        elif action == "cancel":
            self.runtime.cancel_install()
            job = self.installation_jobs.current("workflow-runtime")
            if job:
                self.installation_jobs.update(
                    str(job["id"]),
                    "cancelling",
                    "Cancelling",
                    "Stopping the active download",
                    cancellation_requested=True,
                )
        elif action == "pause":
            self.runtime.pause_install()
            job = self.installation_jobs.current("workflow-runtime")
            if job:
                self.installation_jobs.update(
                    str(job["id"]),
                    "paused",
                    "Paused",
                    "Download paused; resume continues when supported",
                )
        elif action == "resume":
            self.runtime.resume_install()
            job = self.installation_jobs.current("workflow-runtime")
            if job:
                self.installation_jobs.update(
                    str(job["id"]), "downloading", "Downloading engine archive", "Resuming download"
                )
        elif action == "start":
            self.runtime.start()
            self.runtime.wait_healthy()
        elif action == "stop":
            self.runtime.stop()
        elif action == "remove":
            self.runtime.remove()
        else:
            self.runtime.start()
            self.runtime.wait_healthy(timeout=15)
        self._sync_runtime()
        return self.db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,)) or {}

    def _pose_manifest(self) -> ComponentManifest:
        return next(item for item in self.core.components if item.id == "pose-control")

    def _identity_manifest(self) -> ComponentManifest:
        return next(item for item in self.core.components if item.id == "identity-lock")

    @staticmethod
    def _video_required_nodes() -> set[str]:
        return {
            "CheckpointLoaderSimple",
            "CLIPLoader",
            "CLIPTextEncode",
            "LTXVConditioning",
            "LTXVImgToVideo",
            "ManualSigmas",
            "SamplerCustomAdvanced",
            "VAEDecode",
            "SaveImage",
        }

    def _sync_video_components(self) -> None:
        snapshot = self.runtime.snapshot()
        video_state = "not_installed"
        video_message = "Install and verify the optional LTX-Video model pack"
        if snapshot.state == "ready":
            try:
                import imageio_ffmpeg

                nodes = self.runtime._request_json("/object_info")
                missing = self._video_required_nodes().difference(nodes)
                pack = self._pack_row(VIDEO_MODEL_ALIAS)
                encoder_path = Path(imageio_ffmpeg.get_ffmpeg_exe())
                encoder_ready = (
                    encoder_path.name == VIDEO_FFMPEG_FILENAME
                    and encoder_path.is_file()
                    and encoder_path.stat().st_size == VIDEO_FFMPEG_BYTES
                    and sha256_file(encoder_path) == VIDEO_FFMPEG_SHA256
                )
                if missing:
                    video_state = "repair_needed"
                    video_message = f"Video runtime is missing: {', '.join(sorted(missing))}"
                elif not encoder_ready:
                    video_state = "repair_needed"
                    video_message = "The managed local MP4 encoder is unavailable"
                elif pack["installed"] and pack["verified"]:
                    video_state = "ready"
                    video_message = "Native LTXV sampling and local MP4 encoding are verified"
            except Exception:
                video_state = "repair_needed"
                video_message = "Video runtime verification did not complete; use Verify or Repair"
        self.db.execute(
            "UPDATE engine_components SET state=?, progress=?, last_health_message=?, updated_at=? WHERE id='video-generation'",
            (video_state, 100 if video_state == "ready" else 0, video_message, utc_now()),
        )
        pose = self.db.query_one("SELECT state FROM engine_components WHERE id='pose-control'")
        motion_state = (
            "ready"
            if video_state == "ready" and pose and pose["state"] == "ready"
            else "not_installed"
        )
        motion_message = (
            "Broad-motion trim, DWPose extraction, smoothing, and preview are available"
            if motion_state == "ready"
            else "Verify Pose Control and Image-to-Video to enable Reference Motion"
        )
        self.db.execute(
            "UPDATE engine_components SET state=?, progress=?, last_health_message=?, updated_at=? WHERE id='reference-motion'",
            (motion_state, 100 if motion_state == "ready" else 0, motion_message, utc_now()),
        )

    def _verify_video_components(self) -> None:
        self.runtime.start()
        if not self.runtime.wait_healthy(timeout=45):
            raise ValueError("Start the Local Generation Engine before verifying local video")
        nodes = self.runtime._request_json("/object_info")
        missing = self._video_required_nodes().difference(nodes)
        if missing:
            raise ValueError(f"The local video runtime is missing: {', '.join(sorted(missing))}")
        self.verify_video_pack()
        self._sync_video_components()

    def _sync_image_finishing_component(self) -> None:
        snapshot = self.runtime.snapshot()
        state = snapshot.state
        message = "Install and start the managed Image Workflow Engine to enable local editing"
        if snapshot.state == "ready":
            required = {
                "LoadImage",
                "LoadImageMask",
                "VAEEncodeForInpaint",
                "ImageCompositeMasked",
                "SaveImage",
            }
            try:
                nodes = self.runtime._request_json("/object_info")
                missing = required.difference(nodes)
                state = "ready" if not missing else "repair_needed"
                message = (
                    "Native inpainting, mask compositing, image I/O, and upscaling nodes are verified"
                    if not missing
                    else f"Image Editing runtime is missing: {', '.join(sorted(missing))}"
                )
            except Exception:
                state = "repair_needed"
                message = "Image Editing node verification did not complete; use Verify or Repair"
        self.db.execute(
            "UPDATE engine_components SET state=?, progress=?, last_health_message=?, updated_at=? WHERE id='image-finishing'",
            (state, 100 if state == "ready" else 0, message, utc_now()),
        )

    def _verify_image_finishing_component(self) -> None:
        self.runtime.start()
        if not self.runtime.wait_healthy(timeout=45):
            raise ValueError("Start the Local Generation Engine before verifying Image Editing")
        nodes = self.runtime._request_json("/object_info")
        required = {
            "LoadImage",
            "LoadImageMask",
            "VAEEncodeForInpaint",
            "ImageCompositeMasked",
            "SaveImage",
        }
        if not required.issubset(nodes):
            missing = ", ".join(sorted(required.difference(nodes)))
            raise ValueError(
                f"The local Image Editing runtime is missing required nodes: {missing}"
            )
        self.db.execute(
            "UPDATE engine_components SET state='ready', progress=100, last_health_message=?, updated_at=? WHERE id='image-finishing'",
            (
                "Native inpainting, mask compositing, image I/O, and upscaling nodes are verified",
                utc_now(),
            ),
        )

    def _identity_extension_root(self) -> Path | None:
        layout = self.runtime.installed_layout()
        return layout[0].parent / "custom_nodes" / "ComfyUI_IPAdapter_plus" if layout else None

    def _sync_identity_component(self) -> None:
        root = self._identity_extension_root()
        row = self.db.query_one("SELECT state FROM engine_components WHERE id='identity-lock'")
        if row and row["state"] == "installing":
            return
        if root is None or not (root / ".vanta-component.json").is_file():
            self.db.execute(
                "UPDATE engine_components SET state='not_installed', progress=0, last_health_message=?, updated_at=? WHERE id='identity-lock'",
                ("Install the reviewed local identity-conditioning extension", utc_now()),
            )

    def _identity_component_progress(self, progress: int, message: str) -> None:
        self.db.execute(
            "UPDATE engine_components SET state='installing', progress=?, last_health_message=?, updated_at=? WHERE id='identity-lock'",
            (progress, message, utc_now()),
        )

    def _install_identity_component(self) -> None:
        manifest = self._identity_manifest()
        try:
            root = self._identity_extension_root()
            if root is None:
                raise ValueError("Install the Local Generation Engine before Identity Lock")
            self._identity_component_progress(5, "Downloading the reviewed identity extension")
            archive = self._download_verified(
                str(manifest.source["url"]),
                self.settings.engine_root
                / "downloads"
                / f"identity-lock-{manifest.pinned_revision}.zip",
                int(manifest.source["bytes"]),
                str(manifest.source["sha256"]),
                lambda value: self._identity_component_progress(
                    min(70, 5 + round(value * 0.65)),
                    "Downloading the reviewed identity extension",
                ),
            )
            self.runtime.stop()
            staging = self.settings.engine_root / f"identity-staging-{uuid.uuid4().hex}"
            staging.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(archive) as bundle:
                    ensure_safe_archive_members(bundle.namelist())
                    bundle.extractall(staging)
                source = next(staging.iterdir())
                if root.exists():
                    shutil.rmtree(root)
                shutil.copytree(source, root)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            (root / ".vanta-component.json").write_text(
                json.dumps(
                    {"revision": manifest.pinned_revision, "sha256": manifest.source["sha256"]}
                ),
                encoding="utf-8",
            )
            self._identity_component_progress(90, "Restarting and verifying Identity Lock")
            self._verify_identity_component()
        except Exception as error:
            logger.exception("managed identity component installation failed")
            self.db.execute(
                "UPDATE engine_components SET state='repair_needed', progress=0, last_health_message=?, updated_at=? WHERE id='identity-lock'",
                (str(error), utc_now()),
            )

    def _verify_identity_component(self) -> None:
        root = self._identity_extension_root()
        if root is None or not (root / ".vanta-component.json").is_file():
            raise ValueError("Identity Lock is not installed")
        manifest = self._identity_manifest()
        marker = json.loads((root / ".vanta-component.json").read_text(encoding="utf-8"))
        if marker.get("revision") != manifest.pinned_revision:
            raise ValueError("Identity Lock is out of date; use Repair")
        self.runtime.stop()
        self.runtime.start()
        if not self.runtime.wait_healthy(timeout=90):
            raise ValueError("The local engine did not restart after installing Identity Lock")
        nodes = self.runtime._request_json("/object_info")
        if not {"IPAdapterUnifiedLoader", "IPAdapterAdvanced"}.issubset(nodes):
            raise ValueError("The managed identity-conditioning extension did not load; use Repair")
        self.db.execute(
            "UPDATE engine_components SET state='ready', progress=100, last_health_message=?, updated_at=? WHERE id='identity-lock'",
            ("Identity reference conditioning is installed and verified locally", utc_now()),
        )

    def _remove_identity_component(self) -> None:
        self.runtime.stop()
        root = self._identity_extension_root()
        if root and (root / ".vanta-component.json").is_file():
            shutil.rmtree(root)
        self.db.execute(
            "UPDATE engine_components SET state='not_installed', progress=0, last_health_message=?, updated_at=? WHERE id='identity-lock'",
            ("Identity Lock was removed; character references remain in your library", utc_now()),
        )
        self.runtime.start()

    def _pose_extension_root(self) -> Path | None:
        layout = self.runtime.installed_layout()
        return layout[0].parent / "custom_nodes" / "comfyui_controlnet_aux" if layout else None

    def _sync_pose_component(self) -> None:
        root = self._pose_extension_root()
        row = self.db.query_one("SELECT state FROM engine_components WHERE id='pose-control'")
        if row and row["state"] == "installing":
            return
        if root is None or not (root / ".vanta-component.json").is_file():
            self.db.execute(
                "UPDATE engine_components SET state='not_installed', progress=0, last_health_message=?, updated_at=? WHERE id='pose-control'",
                ("Install the reviewed local pose preprocessor", utc_now()),
            )

    def _component_progress(self, progress: int, message: str) -> None:
        self.db.execute(
            "UPDATE engine_components SET state='installing', progress=?, last_health_message=?, updated_at=? WHERE id='pose-control'",
            (progress, message, utc_now()),
        )

    @staticmethod
    def _patch_dwpose_extension(root: Path) -> None:
        """Remove unused legacy OpenPose imports from the maintained DWPose wrapper."""
        module = root / "src" / "custom_controlnet_aux" / "dwpose" / "__init__.py"
        source = module.read_text(encoding="utf-8")
        original = (
            "from .body import Body, BodyResult, Keypoint\n"
            "from .hand import Hand\n"
            "from .face import Face\n"
            "from .types import PoseResult, HandResult, FaceResult, AnimalPoseResult"
        )
        replacement = (
            "from .types import (\n"
            "    AnimalPoseResult,\n"
            "    BodyResult,\n"
            "    FaceResult,\n"
            "    HandResult,\n"
            "    Keypoint,\n"
            "    PoseResult,\n"
            ")"
        )
        if original not in source:
            raise RuntimeError("The reviewed DWPose compatibility patch no longer applies")
        module.write_text(source.replace(original, replacement, 1), encoding="utf-8")

        utility = root / "src" / "custom_controlnet_aux" / "dwpose" / "util.py"
        source = utility.read_text(encoding="utf-8")
        color_expression = "matplotlib.colors.hsv_to_rgb([ie / float(len(edges)), 1.0, 1.0]) * 255"
        if (
            "import matplotlib" not in source
            or "from .body import BodyResult, Keypoint" not in source
            or color_expression not in source
        ):
            raise RuntimeError("The reviewed DWPose color compatibility patch no longer applies")
        source = source.replace("import matplotlib", "import colorsys", 1)
        source = source.replace(
            "from .body import BodyResult, Keypoint",
            "from .types import BodyResult, Keypoint",
            1,
        )
        source = source.replace(
            color_expression,
            "np.array(colorsys.hsv_to_rgb(ie / float(len(edges)), 1.0, 1.0)) * 255",
            1,
        )
        utility.write_text(source, encoding="utf-8")

    @staticmethod
    def _download_verified(
        url: str,
        destination: Path,
        expected_size: int,
        expected_hash: str,
        progress: Callable[[int], None] | None = None,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        existing = destination.stat().st_size if destination.exists() else 0
        if existing == expected_size and sha256_file(destination) == expected_hash:
            return destination
        if existing >= expected_size:
            destination.unlink(missing_ok=True)
        last_error: URLError | None = None
        for attempt in range(4):
            existing = destination.stat().st_size if destination.exists() else 0
            headers = {"User-Agent": "Vanta/0.1"}
            if existing:
                headers["Range"] = f"bytes={existing}-"
            request = Request(url, headers=headers)
            try:
                with urlopen(request, timeout=45) as response:
                    resumed = existing > 0 and response.status == 206
                    written = existing if resumed else 0
                    with destination.open("ab" if resumed else "wb") as target:
                        while chunk := response.read(1024 * 1024):
                            target.write(chunk)
                            written += len(chunk)
                            if progress:
                                progress(min(95, round(100 * written / expected_size)))
                last_error = None
                break
            except URLError as error:
                last_error = error
                if attempt < 3:
                    time.sleep(2**attempt)
        if last_error is not None:
            raise RuntimeError("Vanta could not download the reviewed component") from last_error
        if destination.stat().st_size != expected_size or sha256_file(destination) != expected_hash:
            destination.unlink(missing_ok=True)
            raise RuntimeError("The managed component download failed verification")
        return destination

    def _install_pose_component(self) -> None:
        manifest = self._pose_manifest()
        try:
            root = self._pose_extension_root()
            layout = self.runtime.installed_layout()
            if root is None or layout is None:
                raise ValueError("Install the Local Generation Engine before Pose Control")
            self._component_progress(5, "Downloading the reviewed pose preprocessor")
            archive = self._download_verified(
                str(manifest.source["url"]),
                self.settings.engine_root
                / "downloads"
                / f"pose-control-{manifest.pinned_revision}.zip",
                int(manifest.source["bytes"]),
                str(manifest.source["sha256"]),
                lambda value: self._component_progress(
                    min(45, 5 + round(value * 0.4)), "Downloading the reviewed pose preprocessor"
                ),
            )
            wheel_metadata = manifest.source["python_wheel"]
            self._component_progress(46, "Downloading the pinned local vision runtime")
            wheel = self._download_verified(
                str(wheel_metadata["url"]),
                self.settings.engine_root / "downloads" / Path(str(wheel_metadata["url"])).name,
                int(wheel_metadata["bytes"]),
                str(wheel_metadata["sha256"]),
                lambda value: self._component_progress(
                    min(54, 46 + round(value * 0.08)), "Downloading the pinned local vision runtime"
                ),
            )
            asset_downloads: list[tuple[dict[str, Any], Path]] = []
            for index, asset in enumerate(manifest.source["model_assets"]):
                self._component_progress(55 + index * 7, f"Downloading {asset['name']}")
                downloaded = self._download_verified(
                    str(asset["url"]),
                    self.settings.engine_root / "downloads" / str(asset["filename"]),
                    int(asset["bytes"]),
                    str(asset["sha256"]),
                    lambda value, index=index, name=str(asset["name"]): self._component_progress(
                        min(68, 55 + index * 7 + round(value * 0.06)), f"Downloading {name}"
                    ),
                )
                asset_downloads.append((asset, downloaded))
            self.runtime.stop()
            staging = self.settings.engine_root / f"pose-staging-{uuid.uuid4().hex}"
            staging.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(archive) as bundle:
                    ensure_safe_archive_members(bundle.namelist())
                    bundle.extractall(staging)
                source = next(staging.iterdir())
                if root.exists():
                    shutil.rmtree(root)
                shutil.copytree(source, root)
                self._patch_dwpose_extension(root)
                asset_root = root / "ckpts" / "yzd-v" / "DWPose"
                asset_root.mkdir(parents=True, exist_ok=True)
                for asset, downloaded in asset_downloads:
                    shutil.copy2(downloaded, asset_root / str(asset["filename"]))
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            self._component_progress(72, "Installing the pinned local vision runtime")
            completed = subprocess.run(
                [
                    str(layout[1]),
                    "-s",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-index",
                    "--no-deps",
                    "--force-reinstall",
                    str(wheel),
                ],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
                timeout=1800,
            )
            if completed.returncode != 0:
                details = completed.stdout.decode("utf-8", errors="replace")[-800:]
                raise RuntimeError(f"Pose dependency installation failed: {details}")
            (root / ".vanta-component.json").write_text(
                json.dumps(
                    {
                        "revision": manifest.pinned_revision,
                        "sha256": manifest.source["sha256"],
                        "vanta_patch": POSE_EXTENSION_PATCH,
                        "python_wheel": {
                            "name": wheel_metadata["name"],
                            "version": wheel_metadata["version"],
                            "sha256": wheel_metadata["sha256"],
                        },
                        "model_assets": [
                            {
                                "filename": asset["filename"],
                                "sha256": asset["sha256"],
                                "bytes": asset["bytes"],
                            }
                            for asset in manifest.source["model_assets"]
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self._component_progress(90, "Restarting and verifying Pose Control")
            self._verify_pose_component()
        except Exception as error:
            logger.exception("managed pose component installation failed")
            self.db.execute(
                "UPDATE engine_components SET state='repair_needed', progress=0, last_health_message=?, updated_at=? WHERE id='pose-control'",
                (str(error), utc_now()),
            )

    def _verify_pose_component(self) -> None:
        root = self._pose_extension_root()
        if root is None or not (root / ".vanta-component.json").is_file():
            raise ValueError("Pose Control is not installed")
        manifest = self._pose_manifest()
        marker = json.loads((root / ".vanta-component.json").read_text(encoding="utf-8"))
        if marker.get("revision") != manifest.pinned_revision:
            raise ValueError("Pose Control is out of date; use Repair")
        if marker.get("vanta_patch") != POSE_EXTENSION_PATCH:
            raise ValueError("Pose Control needs the current Vanta compatibility patch; use Repair")
        for asset in manifest.source["model_assets"]:
            path = root / "ckpts" / "yzd-v" / "DWPose" / str(asset["filename"])
            if (
                not path.is_file()
                or path.stat().st_size != int(asset["bytes"])
                or sha256_file(path) != str(asset["sha256"])
            ):
                raise ValueError(f"{asset['name']} failed verification; use Repair")
        self.runtime.stop()
        self.runtime.start()
        if not self.runtime.wait_healthy(timeout=90):
            raise ValueError("The local engine did not restart after installing Pose Control")
        nodes = self.runtime._request_json("/object_info")
        if "DWPreprocessor" not in nodes:
            raise ValueError("The managed pose preprocessor did not load; use Repair")
        self.db.execute(
            "UPDATE engine_components SET state='ready', progress=100, last_health_message=?, updated_at=? WHERE id='pose-control'",
            ("DWPose extraction and its pinned local assets are verified", utc_now()),
        )

    def _remove_pose_component(self) -> None:
        self.runtime.stop()
        root = self._pose_extension_root()
        if root and (root / ".vanta-component.json").is_file():
            shutil.rmtree(root)
        self.db.execute(
            "UPDATE engine_components SET state='not_installed', progress=0, last_health_message=?, updated_at=? WHERE id='pose-control'",
            ("Pose Control was removed; saved pose assets remain in your library", utc_now()),
        )
        self.runtime.start()

    def _pack_row(self, alias: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM model_packs WHERE alias=?", (alias,))
        if row is None:
            raise KeyError(alias)
        return row

    def import_model(self, source_path: str, alias: str, license_notes: str = "") -> dict[str, Any]:
        if alias not in {"photoreal_balanced", "preview_fast", "photoreal_max"}:
            raise ValueError("Choose a supported Vanta image model profile")
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("Choose an existing local .safetensors checkpoint")
        family = checkpoint_family(validate_safetensors(source))
        expected_family = "FLUX" if alias == "photoreal_max" else "SDXL"
        if family != expected_family:
            raise ValueError(
                f"This checkpoint is {family}; the selected profile requires {expected_family}"
            )
        self.settings.model_root.mkdir(parents=True, exist_ok=True)
        destination = self.settings.model_root / source.name
        if destination.resolve() != source:
            shutil.copy2(source, destination)
        actual_hash = sha256_file(destination)
        metadata = json.loads(self._pack_row(alias)["metadata"])
        metadata.update(
            {
                "filename": destination.name,
                "model_family": f"{family} (pending engine verification)",
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
        adapter_target = self.settings.ipadapter_root / IDENTITY_ADAPTER_FILENAME
        clip_target = self.settings.clip_vision_root / IDENTITY_CLIP_FILENAME
        self.settings.ipadapter_root.mkdir(parents=True, exist_ok=True)
        self.settings.clip_vision_root.mkdir(parents=True, exist_ok=True)
        if adapter_source != adapter_target:
            shutil.copy2(adapter_source, adapter_target)
        if clip_source != clip_target:
            shutil.copy2(clip_source, clip_target)
        metadata = json.loads(self._pack_row(IDENTITY_PACK_ALIAS)["metadata"])
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
        row = self._pack_row(IDENTITY_PACK_ALIAS)
        adapter = self.settings.ipadapter_root / IDENTITY_ADAPTER_FILENAME
        encoder = self.settings.clip_vision_root / IDENTITY_CLIP_FILENAME
        if not adapter.is_file() or not encoder.is_file():
            raise ValueError(
                "Identity Lock is missing its adapter or CLIP Vision encoder; import both to repair"
            )
        validate_safetensors(adapter)
        validate_safetensors(encoder)
        expected = json.loads(row["metadata"])
        download = expected["download"]
        clip = download["clip_vision"]
        if (
            adapter.stat().st_size != int(download["bytes"])
            or sha256_file(adapter) != expected["sha256"]
            or encoder.stat().st_size != int(clip["bytes"])
            or sha256_file(encoder) != clip["sha256"]
        ):
            raise ValueError("Identity Lock model files failed manifest verification; use Repair")
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
        return self._pack_row(IDENTITY_PACK_ALIAS)

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
        header = validate_safetensors(path)
        family = checkpoint_family(header)
        expected_family = "FLUX" if alias == "photoreal_max" else "SDXL"
        if family != expected_family:
            raise ValueError(
                f"This checkpoint is {family}; the {alias} profile requires {expected_family}"
            )
        actual_hash = sha256_file(path)
        self.db.execute(
            "UPDATE model_packs SET state='verifying', progress=80, updated_at=? WHERE alias=?",
            (utc_now(), alias),
        )
        try:
            compiler = FluxWorkflowCompiler() if family == "FLUX" else WorkflowCompiler()
            self.runtime.submit(compiler.diagnostic(path.name), lambda _value, _max: None)
        except Exception as error:
            self.db.execute(
                "UPDATE model_packs SET state='repair_needed', verified=0, progress=0, updated_at=? WHERE alias=?",
                (utc_now(), alias),
            )
            raise ValueError(
                f"The local image engine could not load this {family} checkpoint"
            ) from error
        metadata = json.loads(row["metadata"])
        metadata.update(
            {
                "filename": path.name,
                "sha256": actual_hash,
                "model_family": family,
                "checkpoint_layout": (
                    "self-contained checkpoint with diffusion model, text encoders, and VAE"
                    if family == "FLUX"
                    else "standard checkpoint"
                ),
            }
        )
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

    def _install_pose_pack(self, item_id: str) -> None:
        try:
            row = self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,))
            if row is None:
                raise KeyError(item_id)
            metadata = json.loads(row["metadata"])
            download = metadata["download"]
            destination = self.settings.controlnet_root / Path(metadata["target_path"]).name
            self.db.execute(
                "UPDATE model_packs SET state='installing', progress=1, updated_at=? WHERE id=?",
                (utc_now(), item_id),
            )

            def progress(value: int) -> None:
                self.db.execute(
                    "UPDATE model_packs SET state='installing', progress=?, updated_at=? WHERE id=?",
                    (value, utc_now(), item_id),
                )

            self._download_verified(
                str(download["url"]),
                destination,
                int(download["bytes"]),
                str(metadata["sha256"]),
                progress,
            )
            self.db.execute(
                """UPDATE model_packs SET state='verifying', installed=1, verified=0, progress=97,
                installed_path=?, original_path=?, file_size=?, license_notes=?, imported_at=?, updated_at=? WHERE id=?""",
                (
                    str(destination),
                    str(download["url"]),
                    destination.stat().st_size,
                    metadata["license"]["name"],
                    utc_now(),
                    utc_now(),
                    item_id,
                ),
            )
            self.verify_pose_pack()
        except Exception:
            logger.exception("managed pose model installation failed")
            self.db.execute(
                "UPDATE model_packs SET state='repair_needed', installed=0, verified=0, progress=0, updated_at=? WHERE id=?",
                (utc_now(), item_id),
            )

    def _install_curated_checkpoint(self, item_id: str) -> None:
        """Install one reviewed checkpoint, never a whole provider repository."""
        row = self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,))
        if row is None:
            return
        metadata = json.loads(row["metadata"])
        download = metadata.get("download", {})
        try:
            url, revision, filename = (
                str(download["url"]),
                str(download["source_revision"]),
                str(download["filename"]),
            )
            expected_bytes, expected_hash = int(download["bytes"]), str(metadata["sha256"])
            if len(revision) != 40 or expected_hash.startswith("fixture:"):
                raise ValueError(
                    "The reviewed model manifest is missing an immutable file revision"
                )
            if filename.lower().endswith(".gguf"):
                raise ValueError("GGUF model loading is not supported in this release.")
            destination = self.settings.model_root / filename
            if (
                os.name == "nt"
                and self.settings.data_dir.drive.upper() == "C:"
                and expected_bytes > 500 * 1024**2
            ):
                raise ValueError(
                    "Choose an F: studio-data location before downloading this large model"
                )
            available = shutil.disk_usage(destination.parent).free
            required = expected_bytes * 2 + 10 * 1024**3
            if available < required:
                raise ValueError(
                    f"Not enough disk space. Need {required} bytes including staging and reserve; {available} bytes are available."
                )
            job_id = self.installation_jobs.start(
                row["alias"],
                "install",
                source=url,
                destination=destination,
                total_bytes=expected_bytes,
                resumable=True,
            )
            self.installation_jobs.update(
                job_id,
                "checking_storage",
                "Checking storage",
                "Storage location is suitable for this model",
            )
            self.db.execute(
                "UPDATE model_packs SET state='installing', progress=0, updated_at=? WHERE id=?",
                (utc_now(), item_id),
            )

            def progress(value: int) -> None:
                self.db.execute(
                    "UPDATE model_packs SET state='installing', progress=?, updated_at=? WHERE id=?",
                    (value, utc_now(), item_id),
                )
                downloaded = min(expected_bytes, round(expected_bytes * value / 100))
                self.installation_jobs.update(
                    job_id,
                    "downloading",
                    "Downloading model checkpoint",
                    f"Downloading {filename}",
                    downloaded_bytes=downloaded,
                    total_bytes=expected_bytes,
                    percentage=value,
                )

            self._download_verified(url, destination, expected_bytes, expected_hash, progress)
            self.installation_jobs.update(
                job_id,
                "verifying_download",
                "Verifying download",
                "Verifying exact byte count and SHA-256",
                downloaded_bytes=expected_bytes,
                total_bytes=expected_bytes,
                percentage=96,
            )
            validate_safetensors(destination)
            self.db.execute(
                """UPDATE model_packs SET state='verifying', installed=1, verified=0, progress=97,
                installed_path=?,original_path=?,file_size=?,license_notes=?,imported_at=?,updated_at=? WHERE id=?""",
                (
                    str(destination),
                    url,
                    destination.stat().st_size,
                    metadata["license"]["name"],
                    utc_now(),
                    utc_now(),
                    item_id,
                ),
            )
            self.installation_jobs.update(
                job_id,
                "verifying_installation",
                "Verifying model",
                "Loading the SDXL checkpoint through the local engine",
                percentage=98,
            )
            self.verify_model(row["alias"])
            self.installation_jobs.update(
                job_id,
                "ready",
                "Ready",
                "Model is verified for local SDXL generation",
                percentage=100,
            )
        except Exception as error:
            logger.exception("curated checkpoint installation failed")
            self.db.execute(
                "UPDATE model_packs SET state='repair_needed', installed=0, verified=0, progress=0, updated_at=? WHERE id=?",
                (utc_now(), item_id),
            )
            job = self.installation_jobs.current(str(row["alias"]))
            if job:
                self.installation_jobs.update(
                    str(job["id"]),
                    "failed",
                    "Failed",
                    "Vanta could not verify the reviewed model download.",
                    error_category="model_installation_failed",
                    technical_details=str(error),
                )

    def verify_pose_pack(self) -> dict[str, Any]:
        row = self._pack_row(POSE_PACK_ALIAS)
        metadata = json.loads(row["metadata"])
        path = self.settings.controlnet_root / Path(metadata["target_path"]).name
        if not path.is_file() or path.stat().st_size != int(metadata["download"]["bytes"]):
            raise ValueError("The Pose Control model is missing or incomplete; use Repair")
        validate_safetensors(path)
        if sha256_file(path) != metadata["sha256"]:
            raise ValueError("The Pose Control model failed hash verification; use Repair")
        self.runtime.start()
        if not self.runtime.wait_healthy(timeout=45):
            raise ValueError("Start the Local Generation Engine before verifying Pose Control")
        nodes = self.runtime._request_json("/object_info")
        missing = {"DiffControlNetLoader", "ControlNetApplyAdvanced"} - set(nodes)
        if missing:
            raise ValueError("The local engine is missing compatible Pose Control runtime support")
        self.db.execute(
            """UPDATE model_packs SET state='ready', installed=1, verified=1, progress=100,
            installed_path=?, file_size=?, updated_at=? WHERE alias=?""",
            (str(path), path.stat().st_size, utc_now(), POSE_PACK_ALIAS),
        )
        return self._pack_row(POSE_PACK_ALIAS)

    def _install_identity_pack(self, item_id: str) -> None:
        try:
            row = self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,))
            if row is None:
                raise KeyError(item_id)
            metadata = json.loads(row["metadata"])
            download = metadata["download"]
            clip = download["clip_vision"]
            adapter_path = self.settings.ipadapter_root / IDENTITY_ADAPTER_FILENAME
            clip_path = self.settings.clip_vision_root / IDENTITY_CLIP_FILENAME
            self._download_verified(
                str(download["url"]),
                adapter_path,
                int(download["bytes"]),
                str(metadata["sha256"]),
                lambda value: self.db.execute(
                    "UPDATE model_packs SET state='installing', progress=?, updated_at=? WHERE id=?",
                    (min(25, round(value * 0.25)), utc_now(), item_id),
                ),
            )
            self._download_verified(
                str(clip["url"]),
                clip_path,
                int(clip["bytes"]),
                str(clip["sha256"]),
                lambda value: self.db.execute(
                    "UPDATE model_packs SET state='installing', progress=?, updated_at=? WHERE id=?",
                    (min(95, 25 + round(value * 0.7)), utc_now(), item_id),
                ),
            )
            self.db.execute(
                """UPDATE model_packs SET state='verifying', installed=1, verified=0, progress=97,
                installed_path=?, original_path=?, file_size=?, license_notes=?, imported_at=?, updated_at=? WHERE id=?""",
                (
                    str(adapter_path),
                    str(download["url"]),
                    adapter_path.stat().st_size + clip_path.stat().st_size,
                    metadata["license"]["name"],
                    utc_now(),
                    utc_now(),
                    item_id,
                ),
            )
            self.verify_identity_adapter()
        except Exception:
            logger.exception("managed identity model installation failed")
            self.db.execute(
                "UPDATE model_packs SET state='repair_needed', installed=0, verified=0, progress=0, updated_at=? WHERE id=?",
                (utc_now(), item_id),
            )

    def _install_video_pack(self, item_id: str) -> None:
        try:
            row = self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,))
            if row is None:
                raise KeyError(item_id)
            metadata = json.loads(row["metadata"])
            download = metadata["download"]
            encoder = download["text_encoder"]
            model_path = self.settings.model_root / VIDEO_MODEL_FILENAME
            encoder_path = self.settings.text_encoder_root / VIDEO_TEXT_ENCODER_FILENAME
            self._download_verified(
                str(download["url"]),
                model_path,
                int(download["bytes"]),
                str(metadata["sha256"]),
                lambda value: self.db.execute(
                    "UPDATE model_packs SET state='installing', progress=?, updated_at=? WHERE id=?",
                    (min(48, round(value * 0.48)), utc_now(), item_id),
                ),
            )
            self._download_verified(
                str(encoder["url"]),
                encoder_path,
                int(encoder["bytes"]),
                str(encoder["sha256"]),
                lambda value: self.db.execute(
                    "UPDATE model_packs SET state='installing', progress=?, updated_at=? WHERE id=?",
                    (min(96, 48 + round(value * 0.48)), utc_now(), item_id),
                ),
            )
            self.db.execute(
                """UPDATE model_packs SET state='verifying', installed=1, verified=0, progress=97,
                installed_path=?, original_path=?, file_size=?, license_notes=?, imported_at=?, updated_at=? WHERE id=?""",
                (
                    str(model_path),
                    str(download["url"]),
                    model_path.stat().st_size + encoder_path.stat().st_size,
                    metadata["license"]["name"],
                    utc_now(),
                    utc_now(),
                    item_id,
                ),
            )
            self.verify_video_pack()
        except Exception:
            logger.exception("managed video model installation failed")
            self.db.execute(
                "UPDATE model_packs SET state='repair_needed', installed=0, verified=0, progress=0, updated_at=? WHERE id=?",
                (utc_now(), item_id),
            )

    def verify_video_pack(self) -> dict[str, Any]:
        manifest = next(
            item for item in self.pack_collection.packs if item.alias == VIDEO_MODEL_ALIAS
        )
        metadata = json.loads(manifest.model_dump_json())
        download = metadata["download"]
        encoder = download["text_encoder"]
        model_path = self.settings.model_root / VIDEO_MODEL_FILENAME
        encoder_path = self.settings.text_encoder_root / VIDEO_TEXT_ENCODER_FILENAME
        checks = (
            (model_path, int(download["bytes"]), str(metadata["sha256"])),
            (encoder_path, int(encoder["bytes"]), str(encoder["sha256"])),
        )
        for path, expected_size, expected_hash in checks:
            if not path.is_file() or path.stat().st_size != expected_size:
                raise ValueError(f"{path.name} is missing or incomplete; use Repair")
            if sha256_file(path) != expected_hash:
                raise ValueError(f"{path.name} failed hash verification; use Repair")
        self.runtime.start()
        if not self.runtime.wait_healthy(timeout=45):
            raise ValueError("Start the Local Generation Engine before verifying local video")
        nodes = self.runtime._request_json("/object_info")
        missing = self._video_required_nodes().difference(nodes)
        if missing:
            raise ValueError(f"The local video runtime is missing: {', '.join(sorted(missing))}")
        checkpoint_names = (
            nodes.get("CheckpointLoaderSimple", {})
            .get("input", {})
            .get("required", {})
            .get("ckpt_name", [[]])[0]
        )
        encoder_names = (
            nodes.get("CLIPLoader", {})
            .get("input", {})
            .get("required", {})
            .get("clip_name", [[]])[0]
        )
        if (
            VIDEO_MODEL_FILENAME not in checkpoint_names
            or VIDEO_TEXT_ENCODER_FILENAME not in encoder_names
        ):
            raise ValueError("The Local Generation Engine cannot see the verified LTXV assets")
        metadata.update(
            {
                "filename": VIDEO_MODEL_FILENAME,
                "text_encoder_filename": VIDEO_TEXT_ENCODER_FILENAME,
                "text_encoder_sha256": encoder["sha256"],
                "verification": "exact files and native runtime node registration verified",
            }
        )
        self.db.execute(
            "UPDATE model_packs SET state='ready', installed=1, verified=1, progress=100, metadata=?, updated_at=? WHERE alias=?",
            (json.dumps(metadata), utc_now(), VIDEO_MODEL_ALIAS),
        )
        return self._pack_row(VIDEO_MODEL_ALIAS)

    def pack_action(self, item_id: str, action: str) -> dict[str, Any]:
        if action not in self.allowed_pack_actions:
            raise ValueError("Unsupported model-pack action")
        row = self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,))
        if row is None:
            raise KeyError(item_id)
        if row["alias"] == POSE_PACK_ALIAS and action in {"install", "repair"}:
            thread = self._pack_threads.get(item_id)
            if thread is None or not thread.is_alive():
                self.db.execute(
                    "UPDATE model_packs SET state='installing', progress=1, updated_at=? WHERE id=?",
                    (utc_now(), item_id),
                )
                thread = threading.Thread(
                    target=self._install_pose_pack, args=(item_id,), daemon=True
                )
                self._pack_threads[item_id] = thread
                thread.start()
            return self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,)) or {}
        if row["alias"] == IDENTITY_PACK_ALIAS and action in {"install", "repair"}:
            thread = self._pack_threads.get(item_id)
            if thread is None or not thread.is_alive():
                self.db.execute(
                    "UPDATE model_packs SET state='installing', progress=1, updated_at=? WHERE id=?",
                    (utc_now(), item_id),
                )
                thread = threading.Thread(
                    target=self._install_identity_pack, args=(item_id,), daemon=True
                )
                self._pack_threads[item_id] = thread
                thread.start()
            return self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,)) or {}
        if row["alias"] == VIDEO_MODEL_ALIAS and action in {"install", "repair"}:
            thread = self._pack_threads.get(item_id)
            if thread is None or not thread.is_alive():
                self.db.execute(
                    "UPDATE model_packs SET state='installing', progress=1, updated_at=? WHERE id=?",
                    (utc_now(), item_id),
                )
                thread = threading.Thread(
                    target=self._install_video_pack, args=(item_id,), daemon=True
                )
                self._pack_threads[item_id] = thread
                thread.start()
            return self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,)) or {}
        metadata = json.loads(row["metadata"])
        if action in {"install", "repair"} and metadata.get("download", {}).get("url"):
            thread = self._pack_threads.get(item_id)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(
                    target=self._install_curated_checkpoint, args=(item_id,), daemon=True
                )
                self._pack_threads[item_id] = thread
                thread.start()
            return self.db.query_one("SELECT * FROM model_packs WHERE id=?", (item_id,)) or {}
        if action in {"verify", "repair"}:
            if row["alias"] == POSE_PACK_ALIAS:
                return self.verify_pose_pack()
            if row["alias"] == IDENTITY_PACK_ALIAS:
                return self.verify_identity_adapter()
            if row["alias"] == VIDEO_MODEL_ALIAS:
                return self.verify_video_pack()
            if row["alias"] in {"realesrgan_x2plus", "ultrasharp_x4"}:
                return self.verify_upscaler(row["alias"])
            return self.verify_model(row["alias"])
        if action == "install":
            raise ValueError("This pack is installed with its dedicated import action")
        if action == "remove":
            if row["is_default"]:
                raise ValueError("Choose another verified model before removing the default")
            Path(row.get("installed_path") or "").unlink(missing_ok=True)
            if row["alias"] == IDENTITY_PACK_ALIAS:
                (self.settings.clip_vision_root / IDENTITY_CLIP_FILENAME).unlink(missing_ok=True)
            if row["alias"] == VIDEO_MODEL_ALIAS:
                (self.settings.text_encoder_root / VIDEO_TEXT_ENCODER_FILENAME).unlink(
                    missing_ok=True
                )
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
        active = self.db.query_all(
            "SELECT id,status,progress,current_step,total_steps FROM generation_jobs WHERE status NOT IN ('completed','failed','cancelled') ORDER BY created_at DESC LIMIT 10"
        )
        model = self.db.query_one(
            "SELECT alias, installed_path, verified, metadata FROM model_packs WHERE is_default=1"
        )
        model_metadata = json.loads(model["metadata"]) if model else {}
        logs: list[str] = []
        log_root = self.settings.logs_dir or self.settings.data_dir / "logs"
        for path in sorted(log_root.glob("*.log")):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
            for line in lines:
                sanitized = line
                if self.settings.launch_token:
                    sanitized = sanitized.replace(self.settings.launch_token, "[redacted]")
                sanitized = sanitized.replace(str(Path.home()), "%USERPROFILE%")
                logs.append(f"[{path.name}] {sanitized[:1000]}")
        components = self.list_components()
        packs = self.list_packs()
        return {
            "summary": snapshot.message,
            "messages": [
                "Orchestrator is bound to 127.0.0.1",
                f"ComfyUI {snapshot.revision}: {snapshot.state}",
                f"GPU: {self.hardware['gpu_name']} ({self.hardware['vram_gb']} GB VRAM)",
                "No cloud services are configured",
            ],
            "raw_logs": logs[-240:],
            "system": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "orchestrator_pid": os.getpid(),
                "comfyui_pid": self.runtime.process_id,
                "orchestrator_host": self.settings.host,
                "orchestrator_port": self.settings.port,
                "comfyui_port": snapshot.port,
            },
            "components": [
                {
                    "id": item["id"],
                    "state": item["state"],
                    "version": item["version"],
                    "revision": item["revision"],
                    "source": item["source"],
                    "sha256": item["sha256"],
                    "license": item["license"],
                }
                for item in components
            ],
            "model_packs": [
                {
                    "alias": item["alias"],
                    "state": item["state"],
                    "installed": item["installed"],
                    "verified": item["verified"],
                    "filename": item.get("filename"),
                    "sha256": item.get("sha256"),
                    "license": item.get("license"),
                }
                for item in packs
            ],
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
                "active_jobs": active,
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
            "UPDATE generation_jobs SET status='failed', error_message='Vanta closed before this generation finished', updated_at=? WHERE status IN ('checking_engine', 'starting_engine', 'preparing', 'preparing_prompt', 'applying_loras', 'applying_identity', 'applying_pose', 'loading_model', 'generating', 'decoding', 'encoding', 'saving', 'creating_thumbnail', 'finalizing_metadata', 'cancelling')",
            (utc_now(),),
        )
        if self.db.query_one("SELECT id FROM generation_jobs WHERE status='queued' LIMIT 1"):
            self._start_worker()

    def queue(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id, now = f"job-{uuid.uuid4().hex}", utc_now()
        request = dict(request)
        if request.get("operation") == "inpaint":
            self._persist_inpaint_mask(job_id, request)
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
            self._update(job_id, "checking_engine", 0, started_at=utc_now())
            if self.engine.runtime.snapshot().state != "ready":
                self._update(job_id, "starting_engine", 0)
                self.engine.runtime.start()
            self._update(job_id, "checking_engine", 0)
            if not self.engine.runtime.wait_healthy(timeout=45):
                raise RuntimeError("The Local Generation Engine is not ready")
            if request.get("operation") == "video":
                self._run_video(job_id, request, started)
                return
            if request.get("operation") == "upscale":
                self._run_upscale(job_id, request, started)
                return
            self._update(job_id, "preparing_prompt", 8)
            model = self.engine.model_for_alias(request["model_alias"])
            model_metadata = json.loads(model["metadata"])
            model_family = model_metadata.get("model_family", "SDXL")
            if request.get("lora_ids") or request.get("character_id"):
                self._update(job_id, "applying_loras", 10)
            loras = self._resolve_loras(request, model_family)
            if model_family == "FLUX" and (
                request.get("operation") != "generate"
                or request.get("source_generation_id")
                or request.get("identity_reference_id")
                or request.get("pose_id")
            ):
                raise ValueError(
                    "The Maximum FLUX profile currently supports native text-to-image and FLUX LoRAs; choose Balanced for editing, identity, or pose control"
                )
            inpaint_metadata: dict[str, Any] | None = None
            if request.get("operation") == "inpaint":
                source_image_name, mask_image_name, inpaint_metadata = self._prepare_inpaint_inputs(
                    request
                )
                identity_image_name = None
                pose_image_name, pose_metadata = None, None
            else:
                source_image_name = self._prepare_variation_source(request)
                mask_image_name = None
                if request.get("identity_reference_id"):
                    self._update(job_id, "applying_identity", 12)
                identity_image_name = self._prepare_identity_reference(request)
                if request.get("pose_id"):
                    self._update(job_id, "applying_pose", 14)
                pose_image_name, pose_metadata = self._prepare_pose_control(request)
            if model_family == "FLUX":
                workflow = FluxWorkflowCompiler().compile(
                    request,
                    Path(model["installed_path"]).name,
                    loras,
                )
            elif request.get("operation") == "inpaint":
                workflow = WorkflowCompiler().compile_inpaint(
                    request,
                    Path(model["installed_path"]).name,
                    source_image_name,
                    mask_image_name or "",
                    loras,
                )
            else:
                workflow = WorkflowCompiler().compile(
                    request,
                    Path(model["installed_path"]).name,
                    loras,
                    source_image_name,
                    identity_image_name,
                    pose_image_name,
                    request.get("pose_strength"),
                )

            self._update(job_id, "loading_model", 15)

            def progress(value: int, maximum: int) -> None:
                if self.get(job_id)["status"] == "cancelling":
                    return
                percentage = min(88, 20 + round(68 * value / maximum))
                self._update(
                    job_id,
                    "generating",
                    percentage,
                    current_step=value,
                    total_steps=maximum,
                )

            prompt_id, history = self.engine.runtime.submit(workflow, progress)
            if self.get(job_id)["status"] == "cancelling":
                raise RuntimeError("Generation cancelled")
            self._update(job_id, "decoding", 90, prompt_id=prompt_id)
            output_node = "8" if model_family == "FLUX" else "7"
            output = history.get("outputs", {}).get(output_node, {}).get("images", [])
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
            self._update(job_id, "creating_thumbnail", 97)
            with Image.open(destination) as rendered:
                rendered.thumbnail((480, 480))
                rendered.convert("RGB").save(thumbnail, "JPEG", quality=88, optimize=True)
            metadata = {
                "workflow_version": (
                    FluxWorkflowCompiler.version
                    if model_family == "FLUX"
                    else (
                        "image-sdxl-inpaint-v1"
                        if request.get("operation") == "inpaint"
                        else WorkflowCompiler.workflow_version(
                            source_image=bool(source_image_name),
                            identity_image=bool(identity_image_name),
                            pose_image=bool(pose_image_name),
                        )
                    )
                ),
                "operation": request.get("operation", "generate"),
                "derivative_of": request.get("source_generation_id"),
                "compiled_positive_prompt": (
                    str(request.get("region_prompt") or "").strip()
                    if request.get("operation") == "inpaint"
                    else WorkflowCompiler.compile_prompt(request)
                ),
                "negative_prompt": (
                    request.get("region_negative_prompt", "")
                    if request.get("operation") == "inpaint"
                    else request.get("negative_prompt", "")
                ),
                "model_filename": Path(model["installed_path"]).name,
                "model_sha256": model_metadata.get("sha256"),
                "model_family": model_family,
                "comfyui_revision": self.engine.runtime.revision,
                "steps": request["steps"],
                "guidance": request["guidance"],
                "sampler": request.get("sampler", "euler"),
                "scheduler": request.get("scheduler", "normal"),
                "loras": loras,
                "source_generation_id": request.get("source_generation_id"),
                "variation_strength": request.get("variation_strength"),
                "variation_mode": request.get("variation_mode"),
                "variation_prompt": request.get("variation_prompt"),
                "identity_reference_id": request.get("identity_reference_id"),
                "identity_strength": request.get("identity_strength", 0.6),
                "pose_control": pose_metadata,
                "inpaint": inpaint_metadata,
                "disclosure": True,
                "duration_seconds": round(time.monotonic() - started, 2),
                "request": request,
            }
            self._update(job_id, "finalizing_metadata", 99)
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
            self._update(
                job_id,
                "completed",
                100,
                completed_at=utc_now(),
                result_generation_id=generation_id,
            )
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

    def _run_video(self, job_id: str, request: dict[str, Any], started: float) -> None:
        source_id = str(request.get("source_generation_id") or "")
        source = self.db.query_one("SELECT * FROM generations WHERE id=?", (source_id,))
        source_path = Path(source["image_path"]) if source else Path()
        if (
            source is None
            or not source_path.is_file()
            or not is_owned_path(source_path, self.engine.settings.media_root)
        ):
            raise ValueError("Choose an available Gallery image as the first video frame")
        if source.get("media_type") == "video":
            source_metadata = json.loads(source.get("metadata") or "{}")
            continuation_value = source_metadata.get("continuation_frame_path")
            recorded_continuation = Path(continuation_value) if continuation_value else None
            continuation = (
                recorded_continuation
                if recorded_continuation
                and is_owned_path(recorded_continuation, self.engine.settings.media_root)
                else self.engine.settings.media_root / f"{source_id}.last.png"
            )
            if not continuation.is_file():
                extract_last_frame(source_path, continuation)
            source_metadata["continuation_frame_path"] = str(continuation)
            self.db.execute(
                "UPDATE generations SET metadata=? WHERE id=?",
                (json.dumps(source_metadata), source_id),
            )
            source_path = continuation
        pack = self.engine.model_for_alias(VIDEO_MODEL_ALIAS)
        pack_metadata = json.loads(pack["metadata"])
        profile = VIDEO_PROFILES[request["profile"]]
        motion_reference: dict[str, Any] | None = None
        motion_prompt = str(request["motion_prompt"]).strip()
        if request.get("motion_asset_id"):
            motion = self.db.query_one(
                "SELECT * FROM motion_assets WHERE id=?", (request["motion_asset_id"],)
            )
            if motion is None or motion["status"] != "ready":
                raise ValueError("The selected Reference Motion asset is not ready")
            motion_metadata = json.loads(motion["metadata"])
            broad_prompt = str(motion_metadata.get("broad_motion_prompt") or "").strip()
            if broad_prompt:
                motion_prompt = f"{motion_prompt} {broad_prompt}"
            motion_reference = {
                "id": motion["id"],
                "name": motion["name"],
                "trim": [motion["start_seconds"], motion["end_seconds"]],
                "fit_mode": motion["fit_mode"],
                "smoothing": motion["smoothing"],
                "strength": request["motion_strength"],
                "broad_motion_prompt": broad_prompt,
                "identity_transfer": False,
                "audio_transfer": False,
                "branding_transfer": False,
            }
        request = {**request, "motion_prompt": motion_prompt}
        layout = self.engine.runtime.installed_layout()
        if layout is None:
            raise RuntimeError("The Local Generation Engine is not installed")
        input_dir = layout[0].parent / "input" / "Vanta"
        input_dir.mkdir(parents=True, exist_ok=True)
        source_name = f"{job_id}-video-source.png"
        with Image.open(source_path) as image:
            image.convert("RGB").save(input_dir / source_name, "PNG")
        self._update(job_id, "preparing_prompt", 12)
        workflow = LtxVideoWorkflowCompiler().compile(request, f"Vanta/{source_name}")
        self._update(job_id, "loading_model", 15)

        def progress(value: int, maximum: int) -> None:
            if self.get(job_id)["status"] == "cancelling":
                return
            percentage = min(88, 18 + round(70 * value / max(maximum, 1)))
            self._update(
                job_id,
                "generating",
                percentage,
                current_step=value,
                total_steps=maximum,
            )

        prompt_id, history = self.engine.runtime.submit(workflow, progress)
        if self.get(job_id)["status"] == "cancelling":
            raise RuntimeError("Generation cancelled")
        self._update(job_id, "encoding", 90, prompt_id=prompt_id)
        outputs = history.get("outputs", {}).get("15", {}).get("images", [])
        frame_paths = [
            self.engine.runtime.root / "output" / item.get("subfolder", "") / item["filename"]
            for item in outputs
        ]
        if not frame_paths or not all(path.is_file() for path in frame_paths):
            raise RuntimeError("The local video workflow completed without all output frames")
        generation_id = f"generation-{uuid.uuid4().hex}"
        destination = self.engine.settings.media_root / f"{generation_id}.mp4"
        thumbnail = self.engine.settings.media_root / f"{generation_id}.thumb.jpg"
        continuation = self.engine.settings.media_root / f"{generation_id}.last.png"
        encode_mp4(frame_paths, destination, profile["fps"])
        self._update(job_id, "creating_thumbnail", 96)
        with Image.open(frame_paths[0]) as first:
            first.thumbnail((480, 480))
            first.convert("RGB").save(thumbnail, "JPEG", quality=88, optimize=True)
        shutil.copy2(frame_paths[-1], continuation)
        if self.get(job_id)["status"] == "cancelling":
            destination.unlink(missing_ok=True)
            thumbnail.unlink(missing_ok=True)
            continuation.unlink(missing_ok=True)
            raise RuntimeError("Generation cancelled")
        duration = request["duration_seconds"]
        metadata = {
            "workflow_version": (
                "video-ltxv-reference-motion-v1"
                if motion_reference
                else LtxVideoWorkflowCompiler.version
            ),
            "operation": "video",
            "media_type": "video",
            "derivative_of": source_id,
            "source_generation_id": source_id,
            "motion_prompt": motion_prompt,
            "negative_prompt": request.get("negative_prompt", ""),
            "model_filename": VIDEO_MODEL_FILENAME,
            "model_sha256": pack_metadata.get("sha256"),
            "text_encoder_filename": VIDEO_TEXT_ENCODER_FILENAME,
            "text_encoder_sha256": pack_metadata.get("text_encoder_sha256"),
            "profile": request["profile"],
            "steps": profile["steps"],
            "guidance": 1.0,
            "fps": profile["fps"],
            "frame_count": len(frame_paths),
            "duration_seconds": duration,
            "duration_profile": request.get("duration_profile", "custom"),
            "continuation_frame_path": str(continuation),
            "motion_reference": motion_reference,
            "ffmpeg_version": __import__("imageio_ffmpeg").get_ffmpeg_version(),
            "ffmpeg_sha256": VIDEO_FFMPEG_SHA256,
            "comfyui_revision": self.engine.runtime.revision,
            "disclosure": True,
            "duration_render_seconds": round(time.monotonic() - started, 2),
            "request": request,
        }
        self._update(job_id, "finalizing_metadata", 99)
        self.db.execute(
            """INSERT INTO generations(id, character_id, recipe_id, image_path, thumbnail_path, prompt, negative_prompt, seed, model_alias, width, height, metadata, created_at, media_type)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'video')""",
            (
                generation_id,
                source.get("character_id"),
                str(destination),
                str(thumbnail),
                motion_prompt,
                request.get("negative_prompt", ""),
                request["seed"],
                VIDEO_MODEL_ALIAS,
                profile["width"],
                profile["height"],
                json.dumps(metadata),
                utc_now(),
            ),
        )
        self._update(
            job_id,
            "completed",
            100,
            completed_at=utc_now(),
            result_generation_id=generation_id,
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
        self._update(job_id, "preparing_prompt", 12)
        workflow = WorkflowCompiler.upscale(f"Vanta/{source_name}", model_name)
        self._update(job_id, "loading_model", 15)

        def progress(value: int, maximum: int) -> None:
            if self.get(job_id)["status"] == "cancelling":
                return
            percentage = min(90, 20 + round(70 * value / max(maximum, 1)))
            self._update(job_id, "generating", percentage, current_step=value, total_steps=maximum)

        prompt_id, history = self.engine.runtime.submit(workflow, progress)
        if self.get(job_id)["status"] == "cancelling":
            raise RuntimeError("Generation cancelled")
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
        self._update(job_id, "creating_thumbnail", 97)
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
        self._update(job_id, "finalizing_metadata", 99)
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
        self._update(
            job_id,
            "completed",
            100,
            completed_at=utc_now(),
            result_generation_id=generation_id,
        )

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        self._update(job_id, "cancelling", job["progress"])
        self.engine.runtime.interrupt(job.get("prompt_id"))
        return self.get(job_id)

    def _resolve_loras(
        self, request: dict[str, Any], expected_family: str = "SDXL"
    ) -> list[dict[str, Any]]:
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
            if row["model_family"] != expected_family:
                raise ValueError(
                    f"The selected LoRA is not compatible with the {expected_family} workflow"
                )
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
                    "strength": max(
                        0.0,
                        min(
                            2.0,
                            float(
                                request.get("lora_weights", {}).get(
                                    lora_id,
                                    assignment["strength"]
                                    if assignment
                                    else row["default_strength"],
                                )
                            ),
                        ),
                    ),
                    "clip_strength": max(
                        0.0,
                        min(
                            2.0,
                            float(
                                request.get("lora_clip_weights", {}).get(
                                    lora_id,
                                    assignment["clip_strength"]
                                    if assignment
                                    else row["default_clip_strength"],
                                )
                            ),
                        ),
                    ),
                }
            )
        return resolved

    def _prepare_variation_source(self, request: dict[str, Any]) -> str | None:
        generation_id = request.get("source_generation_id")
        if not generation_id:
            return None
        source = self.db.query_one(
            "SELECT image_path, character_id, metadata FROM generations WHERE id=?",
            (generation_id,),
        )
        if source is None or not Path(source["image_path"]).is_file():
            raise ValueError("The selected source image is no longer available for a variation")
        source_metadata = json.loads(source["metadata"])
        mode = str(request.get("variation_mode") or "general")
        if mode == "preserve_identity":
            if not request.get("character_id") and source.get("character_id"):
                request["character_id"] = source["character_id"]
            if not request.get("identity_reference_id"):
                request["identity_reference_id"] = source_metadata.get("identity_reference_id")
            if not request.get("identity_reference_id") and not request.get("character_id"):
                raise ValueError(
                    "Preserve identity requires a source with a character or identity reference"
                )
        if mode == "preserve_pose" and not request.get("pose_id"):
            pose = source_metadata.get("pose_control") or {}
            request["pose_id"] = pose.get("id")
            request["pose_strength"] = pose.get("strength")
            if not request.get("pose_id"):
                raise ValueError("Preserve pose requires a source created with Pose Control")
        layout = self.engine.runtime.installed_layout()
        if layout is None:
            raise ValueError("Start the Local Generation Engine before creating a variation")
        input_dir = layout[0].parent / "input" / "Vanta"
        input_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{generation_id}.png"
        shutil.copy2(source["image_path"], input_dir / filename)
        return f"Vanta/{filename}"

    def _persist_inpaint_mask(self, job_id: str, request: dict[str, Any]) -> None:
        encoded = request.pop("inpaint_mask_data_url", None)
        existing = request.get("inpaint_mask_path")
        if not encoded:
            if existing and Path(existing).is_file():
                return
            raise ValueError("Paint a mask before starting the inpaint")
        prefix = "data:image/png;base64,"
        if not isinstance(encoded, str) or not encoded.startswith(prefix):
            raise ValueError("The inpaint mask must be a local PNG canvas")
        try:
            raw = base64.b64decode(encoded[len(prefix) :], validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("The inpaint mask data is not valid") from error
        if not raw or len(raw) > 12_000_000:
            raise ValueError("The inpaint mask is empty or too large")
        try:
            with Image.open(BytesIO(raw)) as imported:
                imported.load()
                mask = imported.convert("L")
        except (OSError, ValueError) as error:
            raise ValueError("The inpaint mask PNG could not be read") from error
        if mask.width > 4096 or mask.height > 4096:
            raise ValueError("The inpaint mask exceeds the supported local canvas size")
        if mask.getbbox() is None:
            raise ValueError("Paint at least one region before starting the inpaint")
        destination = self.engine.settings.inpaint_root / f"{job_id}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        mask.save(destination, "PNG", optimize=True)
        request["inpaint_mask_path"] = str(destination)

    def _prepare_inpaint_inputs(self, request: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        generation_id = str(request.get("source_generation_id") or "")
        source = self.db.query_one(
            "SELECT image_path, character_id, recipe_id, width, height FROM generations WHERE id=?",
            (generation_id,),
        )
        if source is None or not Path(source["image_path"]).is_file():
            raise ValueError("The selected source image is no longer available for inpainting")
        mask_path = Path(str(request.get("inpaint_mask_path") or ""))
        if (
            not mask_path.is_file()
            or mask_path.parent.resolve() != self.engine.settings.inpaint_root.resolve()
        ):
            raise ValueError("The persisted inpaint mask is missing")
        with Image.open(source["image_path"]) as original, Image.open(mask_path) as mask:
            if original.size != mask.size:
                raise ValueError("The inpaint mask no longer matches the source image")
            request["width"], request["height"] = original.size
        request["character_id"] = request.get("character_id") or source.get("character_id")
        request["recipe_id"] = request.get("recipe_id") or source.get("recipe_id")
        layout = self.engine.runtime.installed_layout()
        if layout is None:
            raise ValueError("Start the Local Generation Engine before inpainting")
        input_dir = layout[0].parent / "input" / "Vanta"
        input_dir.mkdir(parents=True, exist_ok=True)
        source_name = f"{generation_id}-inpaint-source.png"
        mask_name = f"{request.get('seed', 0)}-{mask_path.name}"
        shutil.copy2(source["image_path"], input_dir / source_name)
        shutil.copy2(mask_path, input_dir / mask_name)
        return (
            f"Vanta/{source_name}",
            f"Vanta/{mask_name}",
            {
                "mask_path": str(mask_path),
                "mask_sha256": sha256_file(mask_path),
                "region_prompt": request.get("region_prompt"),
                "region_negative_prompt": request.get("region_negative_prompt"),
                "denoise_strength": request.get("inpaint_strength"),
                "outside_mask_composite": True,
                "mask_grow_pixels": 12,
            },
        )

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

    def _prepare_pose_control(
        self, request: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any] | None]:
        pose_id = request.get("pose_id")
        if not pose_id:
            return None, None
        pose = self.db.query_one("SELECT * FROM pose_assets WHERE id=?", (pose_id,))
        if pose is None or not Path(pose["control_path"]).is_file():
            raise ValueError("The selected pose control image is no longer available")
        if pose["character_id"] and pose["character_id"] != request.get("character_id"):
            raise ValueError("This pose belongs to a different character")
        model = self.engine.settings.controlnet_root / POSE_CONTROL_FILENAME
        if not model.is_file():
            raise ValueError(
                "Install the Vanta Xinsir OpenPose SDXL control model before using a saved pose"
            )
        layout = self.engine.runtime.installed_layout()
        if layout is None:
            raise ValueError("Start the Local Generation Engine before using a saved pose")
        input_dir = layout[0].parent / "input" / "Vanta"
        input_dir.mkdir(parents=True, exist_ok=True)
        filename = f"pose-{pose_id}.png"
        shutil.copy2(pose["control_path"], input_dir / filename)
        request["pose_strength"] = (
            request.get("pose_strength")
            if request.get("pose_strength") is not None
            else pose["strength"]
        )
        metadata = {
            key: pose[key]
            for key in (
                "id",
                "name",
                "scope",
                "strength",
                "source_sha256",
                "control_sha256",
                "crop_settings",
                "preprocessor_revision",
                "workflow_pack_version",
            )
        }
        metadata["strength"] = request["pose_strength"]
        return f"Vanta/{filename}", metadata

    def get(self, job_id: str) -> dict[str, Any]:
        job = self.db.query_one("SELECT * FROM generation_jobs WHERE id=?", (job_id,))
        if job is None:
            raise KeyError(job_id)
        return self._present(job)

    def _present(self, job: dict[str, Any]) -> dict[str, Any]:
        result = dict(job)
        request = json.loads(result.get("request_json") or "{}")
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
        result["eta_seconds"] = None
        result["elapsed_seconds"] = 0
        started_at = result.get("started_at")
        completed_at = result.get("completed_at")
        if started_at:
            end = datetime.fromisoformat(completed_at) if completed_at else datetime.now(UTC)
            result["elapsed_seconds"] = max(
                0, round((end - datetime.fromisoformat(started_at)).total_seconds())
            )
        if (
            started_at
            and result.get("current_step")
            and result.get("total_steps")
            and 0 < result["current_step"] < result["total_steps"]
        ):
            elapsed = result["elapsed_seconds"]
            result["eta_seconds"] = math.ceil(
                1.1
                * elapsed
                * (result["total_steps"] - result["current_step"])
                / result["current_step"]
            )
        operation = request.get("operation") or "generate"
        model_alias = request.get("model_alias") or (
            VIDEO_MODEL_ALIAS if operation == "video" else "photoreal_balanced"
        )
        width, height = request.get("width"), request.get("height")
        if operation == "video":
            profile = VIDEO_PROFILES.get(request.get("profile", "safe"), VIDEO_PROFILES["safe"])
            width, height = profile["width"], profile["height"]
        elif operation == "upscale" and request.get("source_generation_id"):
            source = self.db.query_one(
                "SELECT width,height FROM generations WHERE id=?",
                (request["source_generation_id"],),
            )
            if source:
                scale = 4 if request.get("upscale_profile") == "ultrasharp_x4" else 2
                width, height = source["width"] * scale, source["height"] * scale
        result["operation"] = operation
        result["model_alias"] = model_alias
        result["model_family"] = (
            "FLUX"
            if model_alias == "photoreal_max"
            else ("LTX-Video" if model_alias == VIDEO_MODEL_ALIAS else "SDXL")
        )
        result["output_width"] = width
        result["output_height"] = height
        result["progress_determinate"] = result["status"] in {"generating", "completed"}
        return result
