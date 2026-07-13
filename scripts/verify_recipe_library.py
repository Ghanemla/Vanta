from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from vanta_orchestrator.config import Settings
from vanta_orchestrator.database import Database
from vanta_orchestrator.repositories import RecipeRepository
from vanta_orchestrator.schemas import RecipeInput


def settings_for(data_dir: Path) -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    return Settings(
        data_dir=data_dir,
        project_root=project_root,
        resource_root=project_root,
        logs_dir=data_dir / "logs",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist and verify an Essential V1 recipe against real local Vanta assets"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ["APPDATA"]) / "studio.vanta.desktop",
    )
    args = parser.parse_args()
    settings = settings_for(args.data_dir.resolve())
    database = Database(
        settings.database_path,
        settings.migrations_dir,
        settings.starter_presets_path,
    )
    database.migrate()
    recipes = RecipeRepository(database)

    character = database.query_one(
        """SELECT c.id,c.name FROM characters c WHERE c.archived=0
        ORDER BY (SELECT COUNT(*) FROM character_references r WHERE r.character_id=c.id) DESC,
        c.updated_at DESC LIMIT 1"""
    )
    model = database.query_one(
        "SELECT alias,installed_path,metadata FROM model_packs WHERE is_default=1 AND verified=1"
    )
    if not character or not model:
        raise RuntimeError("A real character and verified default model are required")

    preset_rows = database.query_all(
        "SELECT id,category FROM presets WHERE origin='builtin' ORDER BY category,name"
    )
    preset_by_category: dict[str, str] = {}
    for preset in preset_rows:
        preset_by_category.setdefault(preset["category"], preset["id"])
    required_categories = {
        "identity_modifier",
        "wardrobe",
        "expression",
        "pose",
        "location",
        "lighting",
        "camera",
        "quality",
        "negative",
        "motion",
    }
    if required_categories - preset_by_category.keys():
        raise RuntimeError(
            "The complete Essential V1 starter preset catalog is not installed"
        )

    reference = database.query_one(
        "SELECT id FROM character_references WHERE character_id=? ORDER BY is_primary DESC,position LIMIT 1",
        (character["id"],),
    )
    pose = database.query_one(
        "SELECT id,strength FROM pose_assets WHERE status='ready' AND (scope='global' OR character_id=?) ORDER BY updated_at DESC LIMIT 1",
        (character["id"],),
    )
    lora = database.query_one(
        "SELECT id,default_strength,default_clip_strength FROM lora_packs WHERE verification_state='ready' ORDER BY updated_at DESC LIMIT 1"
    )
    motion = database.query_one(
        "SELECT id,strength FROM motion_assets WHERE status='ready' ORDER BY updated_at DESC LIMIT 1"
    )

    name = "Essential V1 Acceptance Recipe"
    existing = database.query_all("SELECT id FROM recipes WHERE name=?", (name,))
    for item in existing:
        recipes.delete(item["id"])

    model_metadata = json.loads(model["metadata"] or "{}")
    payload = RecipeInput(
        name=name,
        character_id=character["id"],
        freeform_prompt="original adult character, restrained premium editorial portrait",
        negative_prompt="text, watermark, logo, distorted anatomy",
        model_profile=model["alias"],
        preset_ids=[
            preset_by_category[category] for category in sorted(required_categories)
        ],
        scope="project",
        scope_id="Essential V1 acceptance",
        favorite=True,
        tags=["essential-v1", "verified-local"],
        model_family=model_metadata.get("model_family", "SDXL"),
        model_file=Path(model["installed_path"]).name
        if model["installed_path"]
        else "",
        lora_stack=(
            [
                {
                    "id": lora["id"],
                    "strength": lora["default_strength"],
                    "clip_strength": lora["default_clip_strength"],
                }
            ]
            if lora
            else []
        ),
        identity_settings={"reference_id": reference["id"], "strength": 0.6}
        if reference
        else {},
        pose_settings={"pose_id": pose["id"], "strength": pose["strength"]}
        if pose
        else {},
        variation_settings={"mode": "lighting", "strength": 0.38},
        video_settings={
            "profile": "safe",
            "duration_seconds": 2,
            "motion_asset_id": motion["id"] if motion else None,
            "motion_strength": motion["strength"] if motion else 0.65,
        },
        generation_settings={
            "mode": "studio",
            "width": 768,
            "height": 1024,
            "steps": 25,
            "guidance": 5.5,
            "sampler": "dpmpp_2m",
            "scheduler": "karras",
        },
    )
    created = recipes.create(payload)
    restored = recipes.get(created["id"])
    if restored["generation_settings"] != payload.generation_settings:
        raise RuntimeError(
            "Generation settings did not survive the database round trip"
        )
    if set(restored["preset_ids"]) != set(payload.preset_ids):
        raise RuntimeError("Preset composition did not survive the database round trip")

    print(
        json.dumps(
            {
                "recipe_id": restored["id"],
                "character_id": restored["character_id"],
                "model_profile": restored["model_profile"],
                "model_file": restored["model_file"],
                "preset_count": len(restored["preset_ids"]),
                "lora_ids": [item["id"] for item in restored["lora_stack"]],
                "identity": restored["identity_settings"],
                "pose": restored["pose_settings"],
                "video": restored["video_settings"],
                "generation": restored["generation_settings"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
