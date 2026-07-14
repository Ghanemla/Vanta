from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import imageio_ffmpeg
from PIL import Image, ImageOps

from .config import Settings
from .database import Database, utc_now


@dataclass(frozen=True, slots=True)
class MediaFile:
    path: Path
    mime_type: str
    entity_type: str
    entity_id: str
    variant: str


class MediaAccessError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class MediaService:
    """Resolve and repair Vanta-owned media without exposing arbitrary paths."""

    ENTITY_TYPES = {
        "generation",
        "pose",
        "training-image",
        "training-validation",
        "character-reference",
        "motion",
    }

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    @staticmethod
    def _mime(path: Path, fallback: str) -> str:
        return mimetypes.guess_type(path.name)[0] or fallback

    @staticmethod
    def _inside(path: Path, root: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(root.resolve(strict=False))
            return True
        except ValueError:
            return False

    def _require_owned(
        self,
        value: str | None,
        root: Path,
        *,
        entity_type: str,
        entity_id: str,
        variant: str,
        missing_message: str,
    ) -> Path:
        if not value:
            raise MediaAccessError(404, "media_missing", missing_message)
        path = Path(value).expanduser().resolve(strict=False)
        if not self._inside(path, root):
            raise MediaAccessError(
                403,
                "media_outside_vanta_storage",
                "The recorded media path is outside Vanta-owned storage.",
            )
        if not path.is_file():
            self._index_missing(entity_type, entity_id, variant, path)
            raise MediaAccessError(404, "media_missing", missing_message)
        return path

    def _index_missing(self, entity_type: str, entity_id: str, variant: str, path: Path) -> None:
        self.db.execute(
            """INSERT INTO media_assets(entity_type,entity_id,variant,path,mime_type,file_size,state,verified_at)
            VALUES(?,?,?,?,?,0,'missing',?)
            ON CONFLICT(entity_type,entity_id,variant) DO UPDATE SET
              path=excluded.path,mime_type=excluded.mime_type,file_size=0,state='missing',verified_at=excluded.verified_at""",
            (entity_type, entity_id, variant, str(path), "application/octet-stream", utc_now()),
        )

    def _index_ready(
        self,
        entity_type: str,
        entity_id: str,
        variant: str,
        path: Path,
        mime_type: str,
        *,
        width: int | None = None,
        height: int | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        self.db.execute(
            """INSERT INTO media_assets(entity_type,entity_id,variant,path,mime_type,file_size,width,height,duration_seconds,state,verified_at)
            VALUES(?,?,?,?,?,?,?,?,?,'ready',?)
            ON CONFLICT(entity_type,entity_id,variant) DO UPDATE SET
              path=excluded.path,mime_type=excluded.mime_type,file_size=excluded.file_size,
              width=excluded.width,height=excluded.height,duration_seconds=excluded.duration_seconds,
              state='ready',verified_at=excluded.verified_at""",
            (
                entity_type,
                entity_id,
                variant,
                str(path),
                mime_type,
                path.stat().st_size,
                width,
                height,
                duration_seconds,
                utc_now(),
            ),
        )

    def resolve(self, entity_type: str, entity_id: str, variant: str) -> MediaFile:
        if entity_type not in self.ENTITY_TYPES:
            raise MediaAccessError(404, "media_entity_unknown", "Media entity type was not found.")
        resolver = {
            "generation": self._generation,
            "pose": self._pose,
            "training-image": self._training_image,
            "training-validation": self._training_validation,
            "character-reference": self._character_reference,
            "motion": self._motion,
        }[entity_type]
        path, mime_type = resolver(entity_id, variant)
        self._index_ready(entity_type, entity_id, variant, path, mime_type)
        return MediaFile(path, mime_type, entity_type, entity_id, variant)

    def _generation(self, entity_id: str, variant: str) -> tuple[Path, str]:
        row = self.db.query_one("SELECT * FROM generations WHERE id=?", (entity_id,))
        if row is None:
            raise MediaAccessError(404, "media_record_missing", "Generation record was not found.")
        media_type = row.get("media_type") or "image"
        if variant in {"thumbnail", "poster"}:
            field, fallback = "thumbnail_path", "image/jpeg"
        elif variant == "original" and media_type == "image":
            field, fallback = "image_path", "image/png"
        elif variant == "video" and media_type == "video":
            field, fallback = "image_path", "video/mp4"
        elif variant == "continuation" and media_type == "video":
            metadata = json.loads(row.get("metadata") or "{}")
            value = metadata.get("continuation_frame_path")
            path = self._require_owned(
                value,
                self.settings.media_root,
                entity_type="generation",
                entity_id=entity_id,
                variant=variant,
                missing_message="The video continuation frame is unavailable.",
            )
            return path, self._mime(path, "image/png")
        elif variant == "mask":
            metadata = json.loads(row.get("metadata") or "{}")
            value = (metadata.get("inpaint") or {}).get("mask_path")
            path = self._require_owned(
                value,
                self.settings.inpaint_root,
                entity_type="generation",
                entity_id=entity_id,
                variant=variant,
                missing_message="The inpainting mask is unavailable.",
            )
            return path, "image/png"
        else:
            raise MediaAccessError(
                404, "media_variant_unknown", "That generation media variant is unavailable."
            )
        path = self._require_owned(
            row.get(field),
            self.settings.media_root,
            entity_type="generation",
            entity_id=entity_id,
            variant=variant,
            missing_message=f"The generated {variant} file was not found.",
        )
        return path, self._mime(path, fallback)

    def _pose(self, entity_id: str, variant: str) -> tuple[Path, str]:
        fields = {
            "source": "source_path",
            "source-thumbnail": "source_thumbnail_path",
            "control": "control_path",
            "control-thumbnail": "control_thumbnail_path",
        }
        field = fields.get(variant)
        if field is None:
            raise MediaAccessError(
                404, "media_variant_unknown", "Pose media variant was not found."
            )
        row = self.db.query_one("SELECT * FROM pose_assets WHERE id=?", (entity_id,))
        if row is None:
            raise MediaAccessError(404, "media_record_missing", "Pose record was not found.")
        path = self._require_owned(
            row.get(field),
            self.settings.pose_root,
            entity_type="pose",
            entity_id=entity_id,
            variant=variant,
            missing_message="The pose media file is not ready.",
        )
        return path, self._mime(path, "image/png")

    def _training_image(self, entity_id: str, variant: str) -> tuple[Path, str]:
        field = {"original": "image_path", "thumbnail": "thumbnail_path"}.get(variant)
        if field is None:
            raise MediaAccessError(
                404, "media_variant_unknown", "Training image variant was not found."
            )
        row = self.db.query_one("SELECT * FROM training_images WHERE id=?", (entity_id,))
        if row is None:
            raise MediaAccessError(404, "media_record_missing", "Training image was not found.")
        path = self._require_owned(
            row.get(field),
            self.settings.training_dataset_root,
            entity_type="training-image",
            entity_id=entity_id,
            variant=variant,
            missing_message="The training image file was not found.",
        )
        return path, self._mime(path, "image/png")

    def _training_validation(self, entity_id: str, variant: str) -> tuple[Path, str]:
        if variant != "sample":
            raise MediaAccessError(
                404, "media_variant_unknown", "Training validation variant was not found."
            )
        row = self.db.query_one(
            "SELECT validation_sample_path FROM training_checkpoints WHERE id=?", (entity_id,)
        )
        if row is None:
            raise MediaAccessError(
                404, "media_record_missing", "Training checkpoint was not found."
            )
        path = self._require_owned(
            row.get("validation_sample_path"),
            self.settings.training_run_root,
            entity_type="training-validation",
            entity_id=entity_id,
            variant=variant,
            missing_message="The validation sample was not found.",
        )
        return path, self._mime(path, "image/png")

    def _character_reference(self, entity_id: str, variant: str) -> tuple[Path, str]:
        field = {"original": "image_path", "thumbnail": "thumbnail_path", "crop": "crop_path"}.get(
            variant
        )
        if field is None:
            raise MediaAccessError(
                404, "media_variant_unknown", "Character reference variant was not found."
            )
        row = self.db.query_one("SELECT * FROM character_references WHERE id=?", (entity_id,))
        if row is None:
            raise MediaAccessError(
                404, "media_record_missing", "Character reference was not found."
            )
        path = self._require_owned(
            row.get(field),
            self.settings.reference_root,
            entity_type="character-reference",
            entity_id=entity_id,
            variant=variant,
            missing_message="The character reference file was not found.",
        )
        return path, self._mime(path, "image/jpeg")

    def _motion(self, entity_id: str, variant: str) -> tuple[Path, str]:
        field = {
            "source": "source_path",
            "preview": "preview_path",
            "thumbnail": "thumbnail_path",
        }.get(variant)
        if field is None:
            raise MediaAccessError(
                404, "media_variant_unknown", "Motion media variant was not found."
            )
        row = self.db.query_one("SELECT * FROM motion_assets WHERE id=?", (entity_id,))
        if row is None:
            raise MediaAccessError(404, "media_record_missing", "Motion asset was not found.")
        path = self._require_owned(
            row.get(field),
            self.settings.motion_root,
            entity_type="motion",
            entity_id=entity_id,
            variant=variant,
            missing_message="The motion media file is not ready.",
        )
        fallback = "image/jpeg" if variant == "thumbnail" else "video/mp4"
        return path, self._mime(path, fallback)

    @staticmethod
    def _image_info(path: Path) -> tuple[str, int, int]:
        with Image.open(path) as image:
            image.load()
            mime = Image.MIME.get(image.format or "") or mimetypes.guess_type(path.name)[0]
            return mime or "image/png", image.width, image.height

    @staticmethod
    def _save_thumbnail(source: Path, destination: Path, *, crop: bool = False) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            rendered = ImageOps.exif_transpose(image).convert("RGB")
            if crop:
                rendered = ImageOps.fit(
                    rendered,
                    (512, 512),
                    method=Image.Resampling.LANCZOS,
                    centering=(0.5, 0.35),
                )
            else:
                rendered.thumbnail((480, 480))
            rendered.save(destination, "JPEG", quality=88, optimize=True)

    @staticmethod
    def _video_frames(path: Path) -> tuple[dict[str, Any], Image.Image, Image.Image]:
        reader = imageio_ffmpeg.read_frames(str(path), pix_fmt="rgb24")
        try:
            metadata = next(reader)
            size = tuple(metadata["size"])
            first_bytes = next(reader)
            last_bytes = first_bytes
            for frame in reader:
                last_bytes = frame
            first = Image.frombytes("RGB", size, first_bytes)
            last = Image.frombytes("RGB", size, last_bytes)
            return metadata, first, last
        finally:
            reader.close()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _candidate(self, value: str | None, root: Path, expected_name: str) -> tuple[Path, bool]:
        current = Path(value or "").expanduser().resolve(strict=False)
        if current.is_file() and self._inside(current, root):
            return current, str(current) != (value or "")
        candidate = (root / expected_name).resolve(strict=False)
        return candidate, candidate.is_file() and str(candidate) != (value or "")

    def repair(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "records_scanned": 0,
            "ready_files": 0,
            "normalized_paths": 0,
            "regenerated_derivatives": 0,
            "missing_originals": [],
            "missing_derivatives": [],
            "unsafe_paths": [],
            "invalid_files": [],
        }
        self._repair_generations(report)
        self._repair_poses(report)
        self._repair_references(report)
        self._repair_training(report)
        self._repair_motion(report)
        report["indexed_assets"] = int(
            self.db.query_one("SELECT COUNT(*) AS count FROM media_assets")["count"]
        )
        return report

    def _record_invalid(self, report: dict[str, Any], label: str, error: Exception) -> None:
        report["invalid_files"].append({"media": label, "error": str(error)})

    def _repair_generations(self, report: dict[str, Any]) -> None:
        for row in self.db.query_all("SELECT * FROM generations ORDER BY created_at"):
            report["records_scanned"] += 1
            entity_id = row["id"]
            media_type = row.get("media_type") or "image"
            extension = ".mp4" if media_type == "video" else ".png"
            original, normalized = self._candidate(
                row.get("image_path"), self.settings.media_root, f"{entity_id}{extension}"
            )
            if not original.is_file():
                report["missing_originals"].append(
                    {"entity_type": "generation", "entity_id": entity_id, "path": str(original)}
                )
                self._index_missing(
                    "generation",
                    entity_id,
                    "video" if media_type == "video" else "original",
                    original,
                )
                continue
            if not self._inside(original, self.settings.media_root):
                report["unsafe_paths"].append(str(original))
                continue
            if normalized:
                report["normalized_paths"] += 1
            thumbnail, thumbnail_normalized = self._candidate(
                row.get("thumbnail_path"), self.settings.media_root, f"{entity_id}.thumb.jpg"
            )
            try:
                metadata = json.loads(row.get("metadata") or "{}")
                if media_type == "video":
                    video_meta, first, last = self._video_frames(original)
                    width, height = tuple(video_meta["size"])
                    duration = float(
                        video_meta.get("duration") or metadata.get("duration_seconds") or 0
                    )
                    if not thumbnail.is_file():
                        poster = first.copy()
                        poster.thumbnail((480, 480))
                        poster.save(thumbnail, "JPEG", quality=88, optimize=True)
                        report["regenerated_derivatives"] += 1
                    continuation = self.settings.media_root / f"{entity_id}.last.png"
                    if not continuation.is_file():
                        last.save(continuation, "PNG", optimize=True)
                        report["regenerated_derivatives"] += 1
                    metadata["continuation_frame_path"] = str(continuation.resolve())
                    self._index_ready(
                        "generation",
                        entity_id,
                        "video",
                        original,
                        "video/mp4",
                        width=width,
                        height=height,
                        duration_seconds=duration,
                    )
                    self._index_ready("generation", entity_id, "poster", thumbnail, "image/jpeg")
                    self._index_ready(
                        "generation",
                        entity_id,
                        "continuation",
                        continuation,
                        "image/png",
                        width=width,
                        height=height,
                    )
                else:
                    mime, width, height = self._image_info(original)
                    if not thumbnail.is_file():
                        self._save_thumbnail(original, thumbnail)
                        report["regenerated_derivatives"] += 1
                    self._index_ready(
                        "generation",
                        entity_id,
                        "original",
                        original,
                        mime,
                        width=width,
                        height=height,
                    )
                    self._index_ready("generation", entity_id, "thumbnail", thumbnail, "image/jpeg")
                    mask_path = (metadata.get("inpaint") or {}).get("mask_path")
                    if mask_path:
                        mask = Path(mask_path).resolve(strict=False)
                        if mask.is_file() and self._inside(mask, self.settings.inpaint_root):
                            self._index_ready("generation", entity_id, "mask", mask, "image/png")
                self.db.execute(
                    "UPDATE generations SET image_path=?,thumbnail_path=?,width=?,height=?,metadata=? WHERE id=?",
                    (str(original), str(thumbnail), width, height, json.dumps(metadata), entity_id),
                )
                report["ready_files"] += 2
                if thumbnail_normalized:
                    report["normalized_paths"] += 1
            except Exception as error:
                self._record_invalid(report, f"generation:{entity_id}", error)

    def _repair_poses(self, report: dict[str, Any]) -> None:
        for row in self.db.query_all("SELECT * FROM pose_assets"):
            report["records_scanned"] += 1
            entity_id = row["id"]
            root = self.settings.pose_root / entity_id
            for source_variant, source_field, thumbnail_variant, thumbnail_field in (
                ("source", "source_path", "source-thumbnail", "source_thumbnail_path"),
                ("control", "control_path", "control-thumbnail", "control_thumbnail_path"),
            ):
                source = Path(row.get(source_field) or "").resolve(strict=False)
                if not source.is_file():
                    report["missing_originals"].append(
                        {"entity_type": "pose", "entity_id": entity_id, "variant": source_variant}
                    )
                    continue
                if not self._inside(source, root):
                    report["unsafe_paths"].append(str(source))
                    continue
                try:
                    mime, width, height = self._image_info(source)
                    thumbnail = Path(row.get(thumbnail_field) or "").resolve(strict=False)
                    if not thumbnail.is_file() or not self._inside(thumbnail, root):
                        if thumbnail.is_file():
                            report["unsafe_paths"].append(str(thumbnail))
                        thumbnail = root / f"{source_variant}.thumb.jpg"
                        self._save_thumbnail(source, thumbnail)
                        self.db.execute(
                            f"UPDATE pose_assets SET {thumbnail_field}=? WHERE id=?",
                            (str(thumbnail), entity_id),
                        )
                        report["regenerated_derivatives"] += 1
                    self._index_ready(
                        "pose", entity_id, source_variant, source, mime, width=width, height=height
                    )
                    self._index_ready("pose", entity_id, thumbnail_variant, thumbnail, "image/jpeg")
                    report["ready_files"] += 2
                except Exception as error:
                    self._record_invalid(report, f"pose:{entity_id}:{source_variant}", error)

    def _repair_references(self, report: dict[str, Any]) -> None:
        for row in self.db.query_all("SELECT * FROM character_references"):
            report["records_scanned"] += 1
            entity_id = row["id"]
            source = Path(row.get("image_path") or "").resolve(strict=False)
            if not source.is_file() or not self._inside(source, self.settings.reference_root):
                report["missing_originals"].append(
                    {"entity_type": "character-reference", "entity_id": entity_id}
                )
                continue
            try:
                mime, width, height = self._image_info(source)
                values: dict[str, Path] = {"original": source}
                for variant, field, crop in (
                    ("thumbnail", "thumbnail_path", False),
                    ("crop", "crop_path", True),
                ):
                    path = Path(row.get(field) or "").resolve(strict=False)
                    if not path.is_file() or not self._inside(path, self.settings.reference_root):
                        if path.is_file():
                            report["unsafe_paths"].append(str(path))
                        suffix = ".crop.jpg" if crop else ".thumb.jpg"
                        path = source.with_name(f"{entity_id}{suffix}")
                        self._save_thumbnail(source, path, crop=crop)
                        self.db.execute(
                            f"UPDATE character_references SET {field}=? WHERE id=?",
                            (str(path), entity_id),
                        )
                        report["regenerated_derivatives"] += 1
                    values[variant] = path
                self.db.execute(
                    "UPDATE character_references SET image_path=?,width=?,height=?,sha256=? WHERE id=?",
                    (str(source), width, height, self._sha256(source), entity_id),
                )
                self._index_ready(
                    "character-reference",
                    entity_id,
                    "original",
                    source,
                    mime,
                    width=width,
                    height=height,
                )
                self._index_ready(
                    "character-reference", entity_id, "thumbnail", values["thumbnail"], "image/jpeg"
                )
                self._index_ready(
                    "character-reference",
                    entity_id,
                    "crop",
                    values["crop"],
                    "image/jpeg",
                    width=512,
                    height=512,
                )
                report["ready_files"] += 3
            except Exception as error:
                self._record_invalid(report, f"character-reference:{entity_id}", error)

    def _repair_training(self, report: dict[str, Any]) -> None:
        for row in self.db.query_all("SELECT * FROM training_images"):
            report["records_scanned"] += 1
            entity_id = row["id"]
            source = Path(row.get("image_path") or "").resolve(strict=False)
            if not source.is_file() or not self._inside(
                source, self.settings.training_dataset_root
            ):
                report["missing_originals"].append(
                    {"entity_type": "training-image", "entity_id": entity_id}
                )
                continue
            try:
                mime, width, height = self._image_info(source)
                thumbnail = Path(row.get("thumbnail_path") or "").resolve(strict=False)
                if not thumbnail.is_file() or not self._inside(
                    thumbnail, self.settings.training_dataset_root
                ):
                    if thumbnail.is_file():
                        report["unsafe_paths"].append(str(thumbnail))
                    thumbnail = source.with_name(f"{entity_id}.thumb.jpg")
                    self._save_thumbnail(source, thumbnail)
                    self.db.execute(
                        "UPDATE training_images SET thumbnail_path=? WHERE id=?",
                        (str(thumbnail), entity_id),
                    )
                    report["regenerated_derivatives"] += 1
                self.db.execute(
                    "UPDATE training_images SET image_path=?,width=?,height=? WHERE id=?",
                    (str(source), width, height, entity_id),
                )
                self._index_ready(
                    "training-image",
                    entity_id,
                    "original",
                    source,
                    mime,
                    width=width,
                    height=height,
                )
                self._index_ready("training-image", entity_id, "thumbnail", thumbnail, "image/jpeg")
                report["ready_files"] += 2
            except Exception as error:
                self._record_invalid(report, f"training-image:{entity_id}", error)
        for row in self.db.query_all(
            "SELECT id,validation_sample_path FROM training_checkpoints WHERE validation_sample_path IS NOT NULL"
        ):
            report["records_scanned"] += 1
            path = Path(row["validation_sample_path"]).resolve(strict=False)
            if not path.is_file() or not self._inside(path, self.settings.training_run_root):
                report["missing_originals"].append(
                    {"entity_type": "training-validation", "entity_id": row["id"]}
                )
                continue
            try:
                mime, width, height = self._image_info(path)
                self._index_ready(
                    "training-validation",
                    row["id"],
                    "sample",
                    path,
                    mime,
                    width=width,
                    height=height,
                )
                report["ready_files"] += 1
            except Exception as error:
                self._record_invalid(report, f"training-validation:{row['id']}", error)

    def _repair_motion(self, report: dict[str, Any]) -> None:
        for row in self.db.query_all("SELECT * FROM motion_assets"):
            report["records_scanned"] += 1
            entity_id = row["id"]
            source = Path(row.get("source_path") or "").resolve(strict=False)
            if not source.is_file() or not self._inside(source, self.settings.motion_root):
                report["missing_originals"].append(
                    {"entity_type": "motion", "entity_id": entity_id}
                )
                continue
            self._index_ready(
                "motion", entity_id, "source", source, self._mime(source, "video/mp4")
            )
            report["ready_files"] += 1
            preview = Path(row.get("preview_path") or "").resolve(strict=False)
            if preview.is_file() and self._inside(preview, self.settings.motion_root):
                self._index_ready(
                    "motion", entity_id, "preview", preview, self._mime(preview, "video/mp4")
                )
                report["ready_files"] += 1
            else:
                report["missing_derivatives"].append(
                    {"entity_type": "motion", "entity_id": entity_id, "variant": "preview"}
                )
            thumbnail = Path(row.get("thumbnail_path") or "").resolve(strict=False)
            if not thumbnail.is_file() or not self._inside(thumbnail, self.settings.motion_root):
                if thumbnail.is_file():
                    report["unsafe_paths"].append(str(thumbnail))
                try:
                    video_meta, first, _last = self._video_frames(
                        preview if preview.is_file() else source
                    )
                    thumbnail = self.settings.motion_root / entity_id / "motion-thumbnail.jpg"
                    thumbnail.parent.mkdir(parents=True, exist_ok=True)
                    first.thumbnail((480, 480))
                    first.save(thumbnail, "JPEG", quality=88, optimize=True)
                    self.db.execute(
                        "UPDATE motion_assets SET thumbnail_path=? WHERE id=?",
                        (str(thumbnail), entity_id),
                    )
                    report["regenerated_derivatives"] += 1
                    _ = video_meta
                except Exception as error:
                    self._record_invalid(report, f"motion:{entity_id}:thumbnail", error)
            if thumbnail.is_file() and self._inside(thumbnail, self.settings.motion_root):
                self._index_ready("motion", entity_id, "thumbnail", thumbnail, "image/jpeg")
                report["ready_files"] += 1
