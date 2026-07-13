from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from PIL import Image, ImageChops, ImageFilter

from .config import Settings
from .database import Database, utc_now

VIDEO_MODEL_ALIAS = "video_ltx_2b"
VIDEO_MODEL_FILENAME = "ltxv-2b-0.9.8-distilled-fp8.safetensors"
VIDEO_TEXT_ENCODER_FILENAME = "t5xxl_fp8_e4m3fn.safetensors"
VIDEO_FFMPEG_FILENAME = "ffmpeg-win-x86_64-v7.1.exe"
VIDEO_FFMPEG_BYTES = 87_638_016
VIDEO_FFMPEG_SHA256 = "2ce797a0f88d7f067180338fb227f7b1928ea727bd9a4d7a1d022f7c52af71a3"

VIDEO_PROFILES: dict[str, dict[str, int]] = {
    "safe": {"width": 512, "height": 768, "steps": 8, "fps": 24},
    "balanced": {"width": 576, "height": 768, "steps": 8, "fps": 24},
    "quality": {"width": 640, "height": 832, "steps": 8, "fps": 24},
}


class LtxVideoWorkflowCompiler:
    version = "video-ltxv-i2v-v1"
    distilled_sigmas = "1.0, 0.9937, 0.9875, 0.9812, 0.975, 0.9094, 0.725, 0.4219, 0.0"

    def compile(
        self,
        request: dict[str, Any],
        source_image_name: str,
        checkpoint_name: str = VIDEO_MODEL_FILENAME,
        text_encoder_name: str = VIDEO_TEXT_ENCODER_FILENAME,
    ) -> dict[str, Any]:
        prompt = str(request.get("motion_prompt") or "").strip()
        if not prompt:
            raise ValueError("Describe the intended movement before generating video")
        profile = VIDEO_PROFILES[request["profile"]]
        length = request["duration_seconds"] * profile["fps"] + 1
        return {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": checkpoint_name},
            },
            "2": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": text_encoder_name, "type": "ltxv", "device": "default"},
            },
            "3": {"class_type": "LoadImage", "inputs": {"image": source_image_name}},
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["2", 0]},
            },
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": request.get("negative_prompt", ""),
                    "clip": ["2", 0],
                },
            },
            "6": {
                "class_type": "LTXVConditioning",
                "inputs": {
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "frame_rate": float(profile["fps"]),
                },
            },
            "7": {
                "class_type": "LTXVImgToVideo",
                "inputs": {
                    "positive": ["6", 0],
                    "negative": ["6", 1],
                    "vae": ["1", 2],
                    "image": ["3", 0],
                    "width": profile["width"],
                    "height": profile["height"],
                    "length": length,
                    "batch_size": 1,
                    "strength": request.get("motion_strength", 0.65),
                },
            },
            "8": {"class_type": "RandomNoise", "inputs": {"noise_seed": request["seed"]}},
            "9": {
                "class_type": "CFGGuider",
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["7", 0],
                    "negative": ["7", 1],
                    "cfg": 1.0,
                },
            },
            "10": {
                "class_type": "KSamplerSelect",
                "inputs": {"sampler_name": "euler_ancestral"},
            },
            "11": {
                "class_type": "ManualSigmas",
                "inputs": {"sigmas": self.distilled_sigmas},
            },
            "12": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["8", 0],
                    "guider": ["9", 0],
                    "sampler": ["10", 0],
                    "sigmas": ["11", 0],
                    "latent_image": ["7", 2],
                },
            },
            "13": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["12", 0], "vae": ["1", 2]},
            },
            "15": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "Vanta/video-frame", "images": ["13", 0]},
            },
        }


def encode_mp4(frame_paths: list[Path], destination: Path, fps: int) -> None:
    if not frame_paths:
        raise ValueError("No video frames were produced")
    with Image.open(frame_paths[0]) as first:
        size = first.size
    destination.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio_ffmpeg.write_frames(
        str(destination),
        size,
        fps=fps,
        codec="libx264",
        pix_fmt_in="rgb24",
        pix_fmt_out="yuv420p",
        output_params=["-movflags", "+faststart", "-crf", "20"],
    )
    writer.send(None)
    try:
        for path in frame_paths:
            with Image.open(path) as frame:
                writer.send(frame.convert("RGB").tobytes())
    finally:
        writer.close()


class MotionService:
    def __init__(self, db: Database, settings: Settings, engine: Any):
        self.db, self.settings, self.engine = db, settings, engine
        self._threads: dict[str, threading.Thread] = {}

    def recover(self) -> None:
        self.db.execute(
            "UPDATE motion_assets SET status='failed', error_message='Vanta closed before motion extraction finished', updated_at=? WHERE status IN ('queued', 'extracting', 'encoding')",
            (utc_now(),),
        )

    def list(self) -> list[dict[str, Any]]:
        return [
            self._present(row)
            for row in self.db.query_all("SELECT * FROM motion_assets ORDER BY created_at DESC")
        ]

    def get(self, asset_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM motion_assets WHERE id=?", (asset_id,))
        if row is None:
            raise KeyError(asset_id)
        return self._present(row)

    @staticmethod
    def _present(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = json.loads(result.get("metadata") or "{}")
        return result

    def import_video(self, payload: Any) -> dict[str, Any]:
        if not payload.rights_confirmed:
            raise ValueError("Confirm that you have the rights to use this motion reference")
        source = Path(payload.source_path).expanduser().resolve()
        if not source.is_file() or source.suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv"}:
            raise ValueError("Choose a local MP4, MOV, WebM, or MKV motion reference")
        _, source_duration = imageio_ffmpeg.count_frames_and_secs(str(source))
        if payload.end_seconds > source_duration + 0.05:
            raise ValueError("The trim end is beyond the source video duration")
        if payload.end_seconds <= payload.start_seconds:
            raise ValueError("Trim end must be after trim start")
        if payload.end_seconds - payload.start_seconds > 4.01:
            raise ValueError("Reference Motion accepts a maximum four-second trim")
        asset_id, now = f"motion-{uuid.uuid4().hex}", utc_now()
        root = self.settings.motion_root / asset_id
        root.mkdir(parents=True, exist_ok=True)
        destination = root / f"source{source.suffix.lower()}"
        shutil.copy2(source, destination)
        self.db.execute(
            """INSERT INTO motion_assets(id, name, source_path, start_seconds, end_seconds, fit_mode, smoothing, strength, status, progress, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)""",
            (
                asset_id,
                payload.name,
                str(destination),
                payload.start_seconds,
                payload.end_seconds,
                payload.fit_mode,
                payload.smoothing,
                payload.strength,
                json.dumps(
                    {
                        "source_duration_seconds": round(source_duration, 3),
                        "rights_confirmed": True,
                        "transfer_policy": "broad movement only; identity, face, voice, name, branding, and watermarks excluded",
                    }
                ),
                now,
                now,
            ),
        )
        self._start(asset_id)
        return self.get(asset_id)

    def update(self, asset_id: str, payload: Any) -> dict[str, Any]:
        current = self.get(asset_id)
        source_duration = float(current["metadata"]["source_duration_seconds"])
        if (
            payload.end_seconds > source_duration + 0.05
            or payload.end_seconds <= payload.start_seconds
        ):
            raise ValueError("Choose a valid trim inside the source duration")
        if payload.end_seconds - payload.start_seconds > 4.01:
            raise ValueError("Reference Motion accepts a maximum four-second trim")
        self.db.execute(
            """UPDATE motion_assets SET name=?, start_seconds=?, end_seconds=?, fit_mode=?, smoothing=?, strength=?, status='queued', progress=0, error_message=NULL, updated_at=? WHERE id=?""",
            (
                payload.name,
                payload.start_seconds,
                payload.end_seconds,
                payload.fit_mode,
                payload.smoothing,
                payload.strength,
                utc_now(),
                asset_id,
            ),
        )
        self._start(asset_id)
        return self.get(asset_id)

    def _start(self, asset_id: str) -> None:
        running = self._threads.get(asset_id)
        if running and running.is_alive():
            return
        thread = threading.Thread(target=self._extract, args=(asset_id,), daemon=True)
        self._threads[asset_id] = thread
        thread.start()

    @staticmethod
    def _pose_workflow(filename: str) -> dict[str, Any]:
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": filename}},
            "2": {
                "class_type": "DWPreprocessor",
                "inputs": {
                    "image": ["1", 0],
                    "detect_hand": "enable",
                    "detect_body": "enable",
                    "detect_face": "disable",
                    "resolution": 512,
                    "bbox_detector": "yolox_l.onnx",
                    "pose_estimator": "dw-ll_ucoco_384.onnx",
                },
            },
            "3": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "Vanta/motion-pose", "images": ["2", 0]},
            },
        }

    def _extract(self, asset_id: str) -> None:
        try:
            row = self.get(asset_id)
            root = self.settings.motion_root / asset_id
            frames_dir = root / "frames"
            pose_dir = root / "poses"
            for directory in (frames_dir, pose_dir):
                if directory.exists():
                    shutil.rmtree(directory)
                directory.mkdir(parents=True)
            duration = row["end_seconds"] - row["start_seconds"]
            fit_filter = (
                "fps=8,scale=512:512:force_original_aspect_ratio=increase,crop=512:512"
                if row["fit_mode"] == "crop"
                else "fps=8,scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2"
            )
            reader = imageio_ffmpeg.read_frames(
                row["source_path"],
                pix_fmt="rgb24",
                input_params=["-ss", str(row["start_seconds"]), "-t", str(duration)],
                output_params=["-vf", fit_filter],
            )
            metadata = next(reader)
            size = tuple(metadata["size"])
            raw_frames: list[Path] = []
            for index, frame in enumerate(reader):
                path = frames_dir / f"frame-{index:04d}.png"
                Image.frombytes("RGB", size, frame).save(path)
                raw_frames.append(path)
            if len(raw_frames) < 2:
                raise RuntimeError("The selected trim did not contain enough frames")
            self.db.execute(
                "UPDATE motion_assets SET status='extracting', progress=20, updated_at=? WHERE id=?",
                (utc_now(), asset_id),
            )
            self.engine.runtime.start()
            if not self.engine.runtime.wait_healthy(timeout=45):
                raise RuntimeError("Start the Local Generation Engine before extracting motion")
            layout = self.engine.runtime.installed_layout()
            if layout is None:
                raise RuntimeError("The Local Generation Engine is not installed")
            input_root = layout[0].parent / "input" / "Vanta"
            input_root.mkdir(parents=True, exist_ok=True)
            pose_frames: list[Path] = []
            previous: Image.Image | None = None
            for index, frame_path in enumerate(raw_frames):
                input_name = f"{asset_id}-frame-{index:04d}.png"
                shutil.copy2(frame_path, input_root / input_name)
                _, history = self.engine.runtime.submit(
                    self._pose_workflow(f"Vanta/{input_name}"), lambda _value, _max: None
                )
                output = history.get("outputs", {}).get("3", {}).get("images", [])
                if not output:
                    raise RuntimeError("DWPose did not return a motion frame")
                image = output[0]
                rendered = (
                    self.engine.runtime.root
                    / "output"
                    / image.get("subfolder", "")
                    / image["filename"]
                )
                current = Image.open(rendered).convert("RGB")
                if previous is not None and row["smoothing"] > 0:
                    current = Image.blend(
                        current,
                        previous,
                        min(0.35, float(row["smoothing"]) * 0.35),
                    ).filter(ImageFilter.GaussianBlur(radius=float(row["smoothing"]) * 0.25))
                pose_path = pose_dir / f"pose-{index:04d}.png"
                current.save(pose_path)
                previous = current.copy()
                pose_frames.append(pose_path)
                self.db.execute(
                    "UPDATE motion_assets SET progress=?, updated_at=? WHERE id=?",
                    (20 + round(65 * (index + 1) / len(raw_frames)), utc_now(), asset_id),
                )
            preview = root / "motion-preview.mp4"
            self.db.execute(
                "UPDATE motion_assets SET status='encoding', progress=90, updated_at=? WHERE id=?",
                (utc_now(), asset_id),
            )
            encode_mp4(pose_frames, preview, 8)
            thumbnail = root / "motion-thumbnail.jpg"
            with Image.open(pose_frames[0]) as first:
                first.thumbnail((480, 480))
                first.save(thumbnail, "JPEG", quality=88)
            broad_motion = self._describe_motion(pose_frames)
            motion_metadata = dict(row["metadata"])
            motion_metadata.update(
                {
                    "sample_fps": 8,
                    "extracted_frames": len(pose_frames),
                    "broad_motion_prompt": broad_motion,
                    "face_extraction": False,
                    "audio_transfer": False,
                    "source_branding_transfer": False,
                }
            )
            self.db.execute(
                """UPDATE motion_assets SET preview_path=?, thumbnail_path=?, status='ready', progress=100, metadata=?, updated_at=? WHERE id=?""",
                (str(preview), str(thumbnail), json.dumps(motion_metadata), utc_now(), asset_id),
            )
        except Exception as error:
            self.db.execute(
                "UPDATE motion_assets SET status='failed', error_message=?, updated_at=? WHERE id=?",
                (str(error), utc_now(), asset_id),
            )

    @staticmethod
    def _describe_motion(paths: list[Path]) -> str:
        centers: list[tuple[float, float]] = []
        coverage: list[float] = []
        for path in paths:
            with Image.open(path) as frame:
                gray = frame.convert("L")
                bounds = ImageChops.difference(gray, Image.new("L", gray.size)).getbbox()
                if bounds:
                    centers.append(((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2))
                    coverage.append((bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) / (512 * 512))
        if len(centers) < 2:
            return "The subject makes a subtle restrained body movement while the camera remains steady."
        dx, dy = centers[-1][0] - centers[0][0], centers[-1][1] - centers[0][1]
        horizontal = (
            "shifts gently to the right"
            if dx > 18
            else "shifts gently to the left"
            if dx < -18
            else "stays centered"
        )
        vertical = (
            "rises slightly"
            if dy < -18
            else "lowers slightly"
            if dy > 18
            else "keeps a level posture"
        )
        expansion = max(coverage, default=0) - min(coverage, default=0)
        limbs = (
            "with an expansive arm and body gesture"
            if expansion > 0.08
            else "with restrained limb movement"
        )
        return f"The subject {horizontal}, {vertical}, {limbs}; preserve the source character identity and exclude any reference-person identity."

    def remove(self, asset_id: str) -> None:
        self.get(asset_id)
        root = self.settings.motion_root / asset_id
        if root.resolve().parent != self.settings.motion_root.resolve():
            raise ValueError("Motion asset path is outside Vanta storage")
        shutil.rmtree(root, ignore_errors=True)
        self.db.execute("DELETE FROM motion_assets WHERE id=?", (asset_id,))
