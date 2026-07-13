from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from .comfy_runtime import sha256_file, validate_safetensors
from .database import Database, utc_now
from .schemas import CharacterInput, CharacterLoraInput, LoraImportInput, PresetInput, RecipeInput


def _decode(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    result = dict(row)
    for field in fields:
        result[field] = json.loads(result[field])
    for field in ("favorite", "archived", "installed", "verified", "is_default"):
        if field in result:
            result[field] = bool(result[field])
    return result


class CharacterRepository:
    def __init__(self, db: Database):
        self.db = db

    def list(self, include_archived: bool = False) -> list[dict[str, Any]]:
        where = "" if include_archived else "WHERE archived = 0"
        return [
            self._decorate(_decode(row, ("reference_assets",)))
            for row in self.db.query_all(f"SELECT * FROM characters {where} ORDER BY name")
        ]

    def _decorate(self, item: dict[str, Any]) -> dict[str, Any]:
        item["references"] = self.db.query_all(
            "SELECT id, thumbnail_path, crop_path, sha256, width, height, position, is_primary, notes, created_at "
            "FROM character_references WHERE character_id=? ORDER BY position, created_at",
            (item["id"],),
        )
        for reference in item["references"]:
            reference["is_primary"] = bool(reference["is_primary"])
        item["loras"] = self.db.query_all(
            "SELECT l.id, l.name, l.model_family, l.trigger_token, cl.position, cl.strength, cl.clip_strength, cl.enabled "
            "FROM character_loras cl JOIN lora_packs l ON l.id=cl.lora_id WHERE cl.character_id=? ORDER BY cl.position",
            (item["id"],),
        )
        for lora in item["loras"]:
            lora["enabled"] = bool(lora["enabled"])
        return item

    def create(self, payload: CharacterInput) -> dict[str, Any]:
        item_id, now = f"character-{uuid.uuid4().hex}", utc_now()
        self.db.execute(
            """INSERT INTO characters
            (id, name, identity_description, default_recipe_id, default_model_profile, hair, eyes, facial_features,
            distinguishing_features, style_notes, body_notes, default_negative_prompt, reference_assets, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id,
                payload.name,
                payload.identity_description,
                payload.default_recipe_id,
                payload.default_model_profile,
                payload.hair,
                payload.eyes,
                payload.facial_features,
                payload.distinguishing_features,
                payload.style_notes,
                payload.body_notes,
                payload.default_negative_prompt,
                json.dumps(payload.reference_assets),
                now,
                now,
            ),
        )
        return self.get(item_id)

    def get(self, item_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM characters WHERE id = ?", (item_id,))
        if row is None:
            raise KeyError(item_id)
        return self._decorate(_decode(row, ("reference_assets",)))

    def update(self, item_id: str, payload: CharacterInput) -> dict[str, Any]:
        self.get(item_id)
        self.db.execute(
            """UPDATE characters SET name=?, identity_description=?, default_recipe_id=?, default_model_profile=?,
            hair=?, eyes=?, facial_features=?, distinguishing_features=?, style_notes=?, body_notes=?,
            default_negative_prompt=?, reference_assets=?, updated_at=? WHERE id=?""",
            (
                payload.name,
                payload.identity_description,
                payload.default_recipe_id,
                payload.default_model_profile,
                payload.hair,
                payload.eyes,
                payload.facial_features,
                payload.distinguishing_features,
                payload.style_notes,
                payload.body_notes,
                payload.default_negative_prompt,
                json.dumps(payload.reference_assets),
                utc_now(),
                item_id,
            ),
        )
        return self.get(item_id)

    def archive(self, item_id: str) -> None:
        self.get(item_id)
        self.db.execute(
            "UPDATE characters SET archived=1, updated_at=? WHERE id=?", (utc_now(), item_id)
        )

    def restore(self, item_id: str) -> dict[str, Any]:
        self.get(item_id)
        self.db.execute(
            "UPDATE characters SET archived=0, updated_at=? WHERE id=?", (utc_now(), item_id)
        )
        return self.get(item_id)

    def duplicate(self, item_id: str) -> dict[str, Any]:
        source = self.get(item_id)
        fields = {key: source[key] for key in CharacterInput.model_fields}
        fields["name"] = f"{source['name']} — Copy"
        fields["reference_assets"] = []
        return self.create(CharacterInput(**fields))

    def delete_permanently(self, item_id: str) -> None:
        self.get(item_id)
        self.db.execute("DELETE FROM characters WHERE id=?", (item_id,))


class ReferenceRepository:
    def __init__(self, db: Database, root: Path):
        self.db, self.root = db, root

    def import_image(self, character_id: str, source_path: str, notes: str) -> dict[str, Any]:
        if self.db.query_one("SELECT id FROM characters WHERE id=?", (character_id,)) is None:
            raise KeyError(character_id)
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("Choose an existing local reference image")
        try:
            with Image.open(source) as probe:
                probe.verify()
            with Image.open(source) as image:
                image.load()
                width, height = image.size
                if width < 256 or height < 256:
                    raise ValueError("Choose a reference image at least 256 pixels on each side")
                rendered = ImageOps.exif_transpose(image).convert("RGB")
        except (OSError, ValueError) as error:
            raise ValueError("Vanta could not read this reference image") from error
        digest = sha256_file(source)
        existing = self.db.query_one(
            "SELECT id FROM character_references WHERE character_id=? AND sha256=?",
            (character_id, digest),
        )
        if existing:
            raise ValueError("This exact reference is already in the character library")
        reference_id, now = f"reference-{uuid.uuid4().hex}", utc_now()
        destination_dir = self.root / character_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{reference_id}.jpg"
        thumbnail = destination_dir / f"{reference_id}.thumb.jpg"
        crop = destination_dir / f"{reference_id}.crop.jpg"
        rendered.save(destination, "JPEG", quality=94, optimize=True)
        preview = rendered.copy()
        preview.thumbnail((480, 480))
        preview.save(thumbnail, "JPEG", quality=88, optimize=True)
        ImageOps.fit(
            rendered, (512, 512), method=Image.Resampling.LANCZOS, centering=(0.5, 0.35)
        ).save(crop, "JPEG", quality=90, optimize=True)
        position = int(
            self.db.query_one(
                "SELECT COUNT(*) AS count FROM character_references WHERE character_id=?",
                (character_id,),
            )["count"]
        )
        primary = 1 if position == 0 else 0
        self.db.execute(
            """INSERT INTO character_references(id, character_id, image_path, thumbnail_path, crop_path, sha256, width, height, position, is_primary, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                reference_id,
                character_id,
                str(destination),
                str(thumbnail),
                str(crop),
                digest,
                width,
                height,
                position,
                primary,
                notes,
                now,
            ),
        )
        return self.get(reference_id)

    def get(self, reference_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM character_references WHERE id=?", (reference_id,))
        if row is None:
            raise KeyError(reference_id)
        row["is_primary"] = bool(row["is_primary"])
        return row

    def update(
        self, reference_id: str, notes: str, position: int, is_primary: bool
    ) -> dict[str, Any]:
        current = self.get(reference_id)
        if is_primary:
            self.db.execute(
                "UPDATE character_references SET is_primary=0 WHERE character_id=?",
                (current["character_id"],),
            )
        self.db.execute(
            "UPDATE character_references SET notes=?, position=?, is_primary=? WHERE id=?",
            (notes, position, int(is_primary), reference_id),
        )
        return self.get(reference_id)

    def delete(self, reference_id: str) -> None:
        item = self.get(reference_id)
        for path in (item["image_path"], item["thumbnail_path"], item["crop_path"]):
            Path(path).unlink(missing_ok=True)
        self.db.execute("DELETE FROM character_references WHERE id=?", (reference_id,))


class LoraRepository:
    def __init__(self, db: Database, root: Path):
        self.db, self.root = db, root

    @staticmethod
    def _family(header: dict[str, Any]) -> str:
        metadata = header.get("__metadata__", {})
        architecture = str(metadata.get("modelspec.architecture", "")).lower()
        keys = " ".join(header.keys()).lower()
        if "stable-diffusion-xl" in architecture or "lora_te1" in keys:
            return "SDXL"
        if "flux" in architecture or "double_blocks" in keys:
            return "FLUX"
        if "wan" in architecture or "lora_unet_blocks" in keys:
            return "WAN"
        return "Unknown"

    def import_lora(self, payload: LoraImportInput) -> dict[str, Any]:
        source = Path(payload.source_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("Choose an existing local .safetensors LoRA")
        header = validate_safetensors(source)
        family = self._family(header)
        if family != "SDXL":
            raise ValueError("This LoRA is not compatible with Vanta's installed SDXL workflow")
        digest = sha256_file(source)
        duplicate = self.db.query_one("SELECT id FROM lora_packs WHERE sha256=?", (digest,))
        if duplicate:
            return self.get(duplicate["id"])
        self.root.mkdir(parents=True, exist_ok=True)
        item_id, now = f"lora-{uuid.uuid4().hex}", utc_now()
        destination = self.root / source.name
        if destination.exists() and sha256_file(destination) != digest:
            destination = self.root / f"{item_id}-{source.name}"
        if not destination.exists():
            shutil.copy2(source, destination)
        self.db.execute(
            """INSERT INTO lora_packs(id, name, filename, installed_path, original_path, sha256, file_size, source_notes, license_notes, model_family, trigger_token, default_strength, default_clip_strength, enabled, verification_state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'ready', ?, ?)""",
            (
                item_id,
                payload.name,
                destination.name,
                str(destination),
                str(source),
                digest,
                destination.stat().st_size,
                payload.source_notes,
                payload.license_notes,
                family,
                payload.trigger_token,
                payload.default_strength,
                payload.default_clip_strength,
                now,
                now,
            ),
        )
        return self.get(item_id)

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM lora_packs ORDER BY name")
        for row in rows:
            row["enabled"] = bool(row["enabled"])
        return rows

    def get(self, item_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM lora_packs WHERE id=?", (item_id,))
        if row is None:
            raise KeyError(item_id)
        row["enabled"] = bool(row["enabled"])
        return row

    def assign(self, character_id: str, payload: CharacterLoraInput) -> dict[str, Any]:
        if self.db.query_one("SELECT id FROM characters WHERE id=?", (character_id,)) is None:
            raise KeyError(character_id)
        lora = self.get(payload.lora_id)
        if lora["model_family"] != "SDXL":
            raise ValueError("Only verified SDXL LoRAs can be assigned to this workflow")
        self.db.execute(
            """INSERT INTO character_loras(character_id, lora_id, position, strength, clip_strength, enabled)
            VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(character_id, lora_id) DO UPDATE SET position=excluded.position, strength=excluded.strength, clip_strength=excluded.clip_strength, enabled=excluded.enabled""",
            (
                character_id,
                payload.lora_id,
                payload.position,
                payload.strength,
                payload.clip_strength,
                int(payload.enabled),
            ),
        )
        return {"character_id": character_id, **payload.model_dump()}

    def remove(self, item_id: str) -> None:
        item = self.get(item_id)
        Path(item["installed_path"]).unlink(missing_ok=True)
        self.db.execute("DELETE FROM lora_packs WHERE id=?", (item_id,))


class PresetRepository:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        return [
            _decode(row, ("tags",))
            for row in self.db.query_all("SELECT * FROM presets ORDER BY category, name")
        ]

    def get(self, item_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM presets WHERE id=?", (item_id,))
        if row is None:
            raise KeyError(item_id)
        return _decode(row, ("tags",))

    def create(self, payload: PresetInput, source_preset_id: str | None = None) -> dict[str, Any]:
        item_id, now = f"preset-{uuid.uuid4().hex}", utc_now()
        self.db.execute(
            """INSERT INTO presets
            (id, category, name, prompt, negative_prompt, tags, favorite, origin, scope, source_preset_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'user', ?, ?, ?, ?)""",
            (
                item_id,
                payload.category,
                payload.name,
                payload.prompt,
                payload.negative_prompt,
                json.dumps(payload.tags),
                int(payload.favorite),
                payload.scope,
                source_preset_id,
                now,
                now,
            ),
        )
        return self.get(item_id)

    def update(self, item_id: str, payload: PresetInput) -> dict[str, Any]:
        current = self.get(item_id)
        if current["origin"] == "builtin":
            copied = payload.model_copy(update={"name": f"{payload.name} — Copy"})
            return self.create(copied, source_preset_id=item_id)
        self.db.execute(
            """UPDATE presets SET category=?, name=?, prompt=?, negative_prompt=?, tags=?, favorite=?, scope=?, updated_at=? WHERE id=?""",
            (
                payload.category,
                payload.name,
                payload.prompt,
                payload.negative_prompt,
                json.dumps(payload.tags),
                int(payload.favorite),
                payload.scope,
                utc_now(),
                item_id,
            ),
        )
        return self.get(item_id)

    def duplicate(self, item_id: str) -> dict[str, Any]:
        source = self.get(item_id)
        return self.create(
            PresetInput(**{key: source[key] for key in PresetInput.model_fields}).model_copy(
                update={"name": f"{source['name']} — Copy"}
            ),
            source_preset_id=item_id,
        )

    def delete(self, item_id: str) -> None:
        item = self.get(item_id)
        if item["origin"] == "builtin":
            raise ValueError("Built-in presets are immutable")
        self.db.execute("DELETE FROM presets WHERE id=?", (item_id,))

    def restore_builtins(self) -> None:
        self.db.seed_presets()


class RecipeRepository:
    def __init__(self, db: Database):
        self.db = db

    def list(self) -> list[dict[str, Any]]:
        return self.db.query_all("SELECT * FROM recipes ORDER BY updated_at DESC")

    def create(self, payload: RecipeInput) -> dict[str, Any]:
        recipe_id, now = f"recipe-{uuid.uuid4().hex}", utc_now()
        self.db.execute(
            """INSERT INTO recipes (id, name, character_id, freeform_prompt, negative_prompt, model_profile, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                recipe_id,
                payload.name,
                payload.character_id,
                payload.freeform_prompt,
                payload.negative_prompt,
                payload.model_profile,
                now,
                now,
            ),
        )
        for position, preset_id in enumerate(payload.preset_ids):
            preset = self.db.query_one("SELECT category FROM presets WHERE id=?", (preset_id,))
            if preset:
                self.db.execute(
                    "INSERT INTO recipe_items(id, recipe_id, preset_id, category, position) VALUES (?, ?, ?, ?, ?)",
                    (
                        f"item-{uuid.uuid4().hex}",
                        recipe_id,
                        preset_id,
                        preset["category"],
                        position,
                    ),
                )
        return self.db.query_one("SELECT * FROM recipes WHERE id=?", (recipe_id,)) or {}
