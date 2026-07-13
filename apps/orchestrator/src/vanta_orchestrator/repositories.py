from __future__ import annotations

import json
import uuid
from typing import Any

from .database import Database, utc_now
from .schemas import CharacterInput, PresetInput, RecipeInput


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
            _decode(row, ("reference_assets",))
            for row in self.db.query_all(f"SELECT * FROM characters {where} ORDER BY name")
        ]

    def create(self, payload: CharacterInput) -> dict[str, Any]:
        item_id, now = f"character-{uuid.uuid4().hex}", utc_now()
        self.db.execute(
            """INSERT INTO characters
            (id, name, identity_description, default_recipe_id, default_model_profile, reference_assets, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id,
                payload.name,
                payload.identity_description,
                payload.default_recipe_id,
                payload.default_model_profile,
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
        return _decode(row, ("reference_assets",))

    def update(self, item_id: str, payload: CharacterInput) -> dict[str, Any]:
        self.get(item_id)
        self.db.execute(
            """UPDATE characters SET name=?, identity_description=?, default_recipe_id=?,
            default_model_profile=?, reference_assets=?, updated_at=? WHERE id=?""",
            (
                payload.name,
                payload.identity_description,
                payload.default_recipe_id,
                payload.default_model_profile,
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
