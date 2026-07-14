from __future__ import annotations

import json
import shutil
import subprocess
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


def is_owned_path(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


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


def extract_last_frame(source: Path, destination: Path) -> Path:
    reader = imageio_ffmpeg.read_frames(str(source), pix_fmt="rgb24")
    try:
        metadata = next(reader)
        size = tuple(metadata["size"])
        last: bytes | None = None
        for frame in reader:
            last = frame
    finally:
        reader.close()
    if last is None:
        raise ValueError("The video does not contain a continuation frame")
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", size, last).save(destination, "PNG", optimize=True)
    return destination


def extract_frame(source: Path, destination: Path, timestamp_seconds: float) -> Path:
    reader = imageio_ffmpeg.read_frames(
        str(source), pix_fmt="rgb24", input_params=["-ss", str(timestamp_seconds)]
    )
    try:
        metadata = next(reader)
        size = tuple(metadata["size"])
        frame = next(reader, None)
    finally:
        reader.close()
    if frame is None:
        raise ValueError("The selected continuation time is outside this video")
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", size, frame).save(destination, "PNG", optimize=True)
    return destination


class VideoSequenceService:
    def __init__(self, db: Database, settings: Settings, engine: Any, jobs: Any):
        self.db, self.settings, self.engine, self.jobs = db, settings, engine, jobs

    def duration_capabilities(self, quality_profile: str = "safe") -> dict[str, Any]:
        if quality_profile not in VIDEO_PROFILES:
            raise ValueError("Choose a supported video quality profile")
        verified_setting = self.db.query_one(
            "SELECT value FROM app_settings WHERE key='video_extended_verified'"
        )
        extended_verified = bool(
            verified_setting
            and verified_setting["value"].lower() == "true"
            and self.engine.hardware.get("vram_gb", 0) >= 12
        )
        historical_rates: list[float] = []
        for row in self.db.query_all(
            "SELECT metadata FROM generations WHERE media_type='video' ORDER BY created_at DESC LIMIT 20"
        ):
            metadata = json.loads(row.get("metadata") or "{}")
            duration = float(metadata.get("duration_seconds") or 0)
            render = float(metadata.get("duration_render_seconds") or 0)
            if duration > 0 and render > 0:
                historical_rates.append(render / duration)
        seconds_per_output_second = (
            round(sum(historical_rates) / len(historical_rates), 1) if historical_rates else 150.0
        )
        quality = VIDEO_PROFILES[quality_profile]

        def estimate(duration: int) -> dict[str, Any]:
            frames = duration * quality["fps"] + 1
            factor = {"safe": 1.0, "balanced": 1.2, "quality": 1.45}[quality_profile]
            return {
                "duration_seconds": duration,
                "frame_count": frames,
                "expected_generation_seconds": round(seconds_per_output_second * duration * factor),
                "estimated_vram_gb": {"safe": 9.5, "balanced": 10.7, "quality": 11.8}[
                    quality_profile
                ],
                "estimated_ram_gb": {"safe": 14, "balanced": 16, "quality": 20}[quality_profile],
                "estimated_disk_mb": round(
                    frames * quality["width"] * quality["height"] * 0.000018, 1
                ),
            }

        return {
            "quality_profile": quality_profile,
            "hardware": self.engine.hardware,
            "max_custom_seconds": 8 if extended_verified else 4,
            "extended_verified": extended_verified,
            "historical_samples": len(historical_rates),
            "profiles": [
                {
                    "id": "safe",
                    "name": "Safe",
                    "verified": True,
                    "enabled": True,
                    **estimate(2),
                },
                {
                    "id": "standard",
                    "name": "Standard",
                    "verified": True,
                    "enabled": True,
                    **estimate(4),
                },
                {
                    "id": "extended",
                    "name": "Extended",
                    "verified": extended_verified,
                    "enabled": extended_verified,
                    "range_seconds": [6, 8],
                    **estimate(6),
                },
            ],
        }

    def validate_duration(self, duration_profile: str, duration_seconds: int) -> None:
        capabilities = self.duration_capabilities()
        maximum = int(capabilities["max_custom_seconds"])
        expected = {"safe": 2, "standard": 4}
        if duration_profile in expected and duration_seconds != expected[duration_profile]:
            raise ValueError(
                f"{duration_profile.title()} video uses {expected[duration_profile]} seconds"
            )
        if duration_profile == "extended" and (
            not capabilities["extended_verified"] or duration_seconds not in {6, 7, 8}
        ):
            raise ValueError("Extended video has not been verified safe on this hardware")
        if duration_seconds > maximum:
            raise ValueError(f"This hardware is currently limited to {maximum} seconds per pass")

    def create(self, name: str, source_generation_id: str) -> dict[str, Any]:
        source = self.db.query_one("SELECT * FROM generations WHERE id=?", (source_generation_id,))
        if source is None:
            raise KeyError(source_generation_id)
        source_path = Path(source.get("image_path") or "")
        if not source_path.is_file() or not is_owned_path(source_path, self.settings.media_root):
            raise ValueError("The selected Gallery source is unavailable")
        item_id, now = f"video-sequence-{uuid.uuid4().hex}", utc_now()
        self.db.execute(
            """INSERT INTO video_sequences(
                id,name,source_generation_id,character_id,metadata,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                item_id,
                name,
                source_generation_id,
                source.get("character_id"),
                json.dumps({"disclosure": True, "workflow": "hardware-safe-segments-v1"}),
                now,
                now,
            ),
        )
        return self.get(item_id)

    def _sync(self, sequence_id: str) -> None:
        for segment in self.db.query_all(
            "SELECT * FROM video_sequence_segments WHERE sequence_id=?", (sequence_id,)
        ):
            if not segment.get("job_id"):
                continue
            job = self.db.query_one(
                "SELECT status,result_generation_id,error_message FROM generation_jobs WHERE id=?",
                (segment["job_id"],),
            )
            if not job:
                continue
            status = job["status"]
            self.db.execute(
                "UPDATE video_sequence_segments SET status=?,generation_id=COALESCE(?,generation_id),metadata=?,updated_at=? WHERE id=?",
                (
                    status,
                    job.get("result_generation_id"),
                    json.dumps({"job_id": segment["job_id"], "error": job.get("error_message")}),
                    utc_now(),
                    segment["id"],
                ),
            )
        states = [
            row["status"]
            for row in self.db.query_all(
                "SELECT status FROM video_sequence_segments WHERE sequence_id=?", (sequence_id,)
            )
        ]
        sequence = self.db.query_one(
            "SELECT final_generation_id FROM video_sequences WHERE id=?", (sequence_id,)
        )
        status = (
            "joined"
            if sequence and sequence.get("final_generation_id")
            else "rendering"
            if any(state not in {"completed", "failed", "cancelled"} for state in states)
            else "failed"
            if any(state == "failed" for state in states)
            else "ready"
            if states
            else "draft"
        )
        self.db.execute(
            "UPDATE video_sequences SET status=?,updated_at=? WHERE id=?",
            (status, utc_now(), sequence_id),
        )

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT id FROM video_sequences ORDER BY created_at DESC")
        return [self.get(row["id"]) for row in rows]

    def get(self, sequence_id: str) -> dict[str, Any]:
        sequence = self.db.query_one("SELECT * FROM video_sequences WHERE id=?", (sequence_id,))
        if sequence is None:
            raise KeyError(sequence_id)
        self._sync(sequence_id)
        sequence = self.db.query_one("SELECT * FROM video_sequences WHERE id=?", (sequence_id,))
        assert sequence is not None
        sequence["metadata"] = json.loads(sequence.get("metadata") or "{}")
        sequence["segments"] = self.db.query_all(
            "SELECT * FROM video_sequence_segments WHERE sequence_id=? ORDER BY position",
            (sequence_id,),
        )
        for segment in sequence["segments"]:
            segment["metadata"] = json.loads(segment.get("metadata") or "{}")
        return sequence

    def add_segment(self, sequence_id: str, payload: Any) -> dict[str, Any]:
        sequence = self.get(sequence_id)
        self.validate_duration(payload.duration_profile, payload.duration_seconds)
        segments = sequence["segments"]
        if segments and segments[-1]["status"] != "completed":
            raise ValueError("Finish the previous segment before adding the next one")
        source_id = payload.source_generation_id or (
            segments[-1]["generation_id"] if segments else sequence["source_generation_id"]
        )
        source = self.db.query_one("SELECT image_path FROM generations WHERE id=?", (source_id,))
        source_path = Path(source.get("image_path") or "") if source else Path()
        if (
            source is None
            or not source_path.is_file()
            or not is_owned_path(source_path, self.settings.media_root)
        ):
            raise ValueError("The selected continuation frame is unavailable")
        request = {
            **payload.model_dump(),
            "source_generation_id": source_id,
            "operation": "video",
            "model_alias": VIDEO_MODEL_ALIAS,
        }
        job = self.jobs.queue(request)
        item_id, now = f"video-segment-{uuid.uuid4().hex}", utc_now()
        self.db.execute(
            """INSERT INTO video_sequence_segments(
                id,sequence_id,position,source_generation_id,job_id,motion_prompt,
                negative_prompt,quality_profile,duration_profile,duration_seconds,seed,
                motion_asset_id,motion_strength,status,metadata,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                item_id,
                sequence_id,
                len(segments),
                source_id,
                job["id"],
                payload.motion_prompt,
                payload.negative_prompt,
                payload.profile,
                payload.duration_profile,
                payload.duration_seconds,
                payload.seed,
                payload.motion_asset_id,
                payload.motion_strength,
                job["status"],
                json.dumps({"job_id": job["id"]}),
                now,
                now,
            ),
        )
        return self.get(sequence_id)

    def continuation_frame(self, generation_id: str, timestamp_seconds: float) -> dict[str, Any]:
        source = self.db.query_one(
            "SELECT * FROM generations WHERE id=? AND media_type='video'", (generation_id,)
        )
        source_path = Path(source.get("image_path") or "") if source else Path()
        if (
            source is None
            or not source_path.is_file()
            or not is_owned_path(source_path, self.settings.media_root)
        ):
            raise KeyError(generation_id)
        metadata = json.loads(source.get("metadata") or "{}")
        duration = float(metadata.get("duration_seconds") or 0)
        if timestamp_seconds > duration:
            raise ValueError("Choose a continuation time inside this video")
        item_id = f"generation-{uuid.uuid4().hex}"
        destination = self.settings.media_root / f"{item_id}.png"
        thumbnail = self.settings.media_root / f"{item_id}.thumb.jpg"
        extract_frame(source_path, destination, timestamp_seconds)
        with Image.open(destination) as frame:
            width, height = frame.size
            frame.thumbnail((480, 480))
            frame.convert("RGB").save(thumbnail, "JPEG", quality=88, optimize=True)
        frame_metadata = {
            "workflow_version": "video-selected-continuation-v1",
            "operation": "video_continuation_frame",
            "derivative_of": generation_id,
            "timestamp_seconds": timestamp_seconds,
            "disclosure": True,
        }
        self.db.execute(
            """INSERT INTO generations(
                id,character_id,image_path,thumbnail_path,prompt,negative_prompt,seed,
                model_alias,width,height,metadata,created_at,media_type
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'image')""",
            (
                item_id,
                source.get("character_id"),
                str(destination),
                str(thumbnail),
                f"Selected continuation frame at {timestamp_seconds:.2f} seconds",
                "",
                source["seed"],
                source["model_alias"],
                width,
                height,
                json.dumps(frame_metadata),
                utc_now(),
            ),
        )
        return self.db.query_one("SELECT * FROM generations WHERE id=?", (item_id,)) or {}

    def reorder(self, sequence_id: str, segment_ids: list[str]) -> dict[str, Any]:
        current = self.db.query_all(
            "SELECT id FROM video_sequence_segments WHERE sequence_id=? ORDER BY position",
            (sequence_id,),
        )
        if {row["id"] for row in current} != set(segment_ids) or len(current) != len(segment_ids):
            raise ValueError("Reorder must include every sequence segment exactly once")
        with self.db.connect() as connection:
            for position, segment_id in enumerate(segment_ids):
                connection.execute(
                    "UPDATE video_sequence_segments SET position=?,updated_at=? WHERE id=? AND sequence_id=?",
                    (position + 10_000, utc_now(), segment_id, sequence_id),
                )
            connection.execute(
                "UPDATE video_sequence_segments SET position=position-10000 WHERE sequence_id=?",
                (sequence_id,),
            )
        return self.get(sequence_id)

    def remove_segment(self, sequence_id: str, segment_id: str) -> dict[str, Any]:
        segment = self.db.query_one(
            "SELECT id FROM video_sequence_segments WHERE id=? AND sequence_id=?",
            (segment_id, sequence_id),
        )
        if segment is None:
            raise KeyError(segment_id)
        self.db.execute("DELETE FROM video_sequence_segments WHERE id=?", (segment_id,))
        rows = self.db.query_all(
            "SELECT id FROM video_sequence_segments WHERE sequence_id=? ORDER BY position",
            (sequence_id,),
        )
        for position, row in enumerate(rows):
            self.db.execute(
                "UPDATE video_sequence_segments SET position=? WHERE id=?", (position, row["id"])
            )
        return self.get(sequence_id)

    def join(self, sequence_id: str, segment_ids: list[str]) -> dict[str, Any]:
        sequence = self.get(sequence_id)
        if len(set(segment_ids)) != len(segment_ids):
            raise ValueError("Choose each sequence segment only once")
        by_id = {segment["id"]: segment for segment in sequence["segments"]}
        if any(segment_id not in by_id for segment_id in segment_ids):
            raise ValueError("Choose segments from this sequence")
        selected = [by_id[segment_id] for segment_id in segment_ids]
        if any(
            segment["status"] != "completed" or not segment["generation_id"] for segment in selected
        ):
            raise ValueError("Only completed segments can be joined")
        generations = [
            self.db.query_one("SELECT * FROM generations WHERE id=?", (segment["generation_id"],))
            for segment in selected
        ]
        if any(item is None for item in generations):
            raise ValueError("One or more selected video files are unavailable")
        typed_generations = [item for item in generations if item is not None]
        if len({(item["width"], item["height"]) for item in typed_generations}) != 1:
            raise ValueError("Join segments rendered with the same quality profile")
        paths = [Path(item["image_path"]) for item in typed_generations]
        if not all(
            path.is_file() and is_owned_path(path, self.settings.media_root) for path in paths
        ):
            raise ValueError("One or more selected video files are missing")
        generation_id = f"generation-{uuid.uuid4().hex}"
        destination = self.settings.media_root / f"{generation_id}.mp4"
        destination.parent.mkdir(parents=True, exist_ok=True)
        concat_file = self.settings.media_root / f"{generation_id}.concat.txt"
        concat_file.write_text(
            "\n".join(f"file '{str(path).replace(chr(39), chr(39) * 2)}'" for path in paths),
            encoding="utf-8",
        )
        try:
            completed = subprocess.run(
                [
                    str(imageio_ffmpeg.get_ffmpeg_exe()),
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(destination),
                ],
                capture_output=True,
                text=True,
                timeout=600,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        finally:
            concat_file.unlink(missing_ok=True)
        if completed.returncode or not destination.is_file():
            raise RuntimeError("The local MP4 join could not finish")
        first_meta = json.loads(typed_generations[0].get("metadata") or "{}")
        last_meta = json.loads(typed_generations[-1].get("metadata") or "{}")
        poster = self.settings.media_root / f"{generation_id}.thumb.jpg"
        poster_source = Path(typed_generations[0].get("thumbnail_path") or "")
        if not poster_source.is_file() or not is_owned_path(
            poster_source, self.settings.media_root
        ):
            destination.unlink(missing_ok=True)
            raise ValueError("The first selected segment poster is unavailable")
        shutil.copy2(poster_source, poster)
        continuation = self.settings.media_root / f"{generation_id}.last.png"
        last_continuation = Path(last_meta.get("continuation_frame_path") or "")
        if last_continuation.is_file() and is_owned_path(
            last_continuation, self.settings.media_root
        ):
            shutil.copy2(last_continuation, continuation)
        else:
            extract_last_frame(destination, continuation)
        duration = sum(
            float(json.loads(item["metadata"]).get("duration_seconds") or 0)
            for item in typed_generations
        )
        metadata = {
            "workflow_version": "video-sequence-join-v1",
            "operation": "video_sequence_join",
            "media_type": "video",
            "sequence_id": sequence_id,
            "segment_ids": segment_ids,
            "segment_generation_ids": [item["id"] for item in typed_generations],
            "duration_seconds": duration,
            "frame_count": sum(
                int(json.loads(item["metadata"]).get("frame_count") or 0)
                for item in typed_generations
            ),
            "fps": first_meta.get("fps", 24),
            "continuation_frame_path": str(continuation),
            "disclosure": True,
        }
        width, height = typed_generations[0]["width"], typed_generations[0]["height"]
        self.db.execute(
            """INSERT INTO generations(
                id,character_id,image_path,thumbnail_path,prompt,negative_prompt,seed,
                model_alias,width,height,metadata,created_at,media_type
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'video')""",
            (
                generation_id,
                sequence.get("character_id"),
                str(destination),
                str(poster),
                " · ".join(segment["motion_prompt"] for segment in selected),
                "",
                selected[0]["seed"],
                VIDEO_MODEL_ALIAS,
                width,
                height,
                json.dumps(metadata),
                utc_now(),
            ),
        )
        self.db.execute(
            "UPDATE video_sequences SET status='joined',final_generation_id=?,metadata=?,updated_at=? WHERE id=?",
            (
                generation_id,
                json.dumps({**sequence["metadata"], "joined_segment_ids": segment_ids}),
                utc_now(),
                sequence_id,
            ),
        )
        return self.get(sequence_id)


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
