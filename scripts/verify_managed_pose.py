from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from vanta_orchestrator.config import Settings
from vanta_orchestrator.database import Database
from vanta_orchestrator.engine import (
    IDENTITY_PACK_ALIAS,
    EngineService,
    GenerationService,
    POSE_PACK_ALIAS,
)
from vanta_orchestrator.pose import PoseService
from vanta_orchestrator.repositories import CharacterRepository, ReferenceRepository
from vanta_orchestrator.schemas import CharacterInput, PoseImportInput


def settings_for(data_dir: Path) -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    return Settings(
        data_dir=data_dir,
        project_root=project_root,
        resource_root=project_root,
        logs_dir=data_dir / "logs",
    )


def wait_for_row(
    db: Database,
    query: str,
    parameters: tuple[str, ...],
    active_states: set[str],
    timeout: int,
) -> dict:
    deadline = time.monotonic() + timeout
    previous: tuple[str, int, str] | None = None
    while time.monotonic() < deadline:
        row = db.query_one(query, parameters)
        if row is None:
            raise RuntimeError("The managed component record disappeared")
        snapshot = (
            str(row["state"]),
            int(row.get("progress") or 0),
            str(row.get("last_health_message") or ""),
        )
        if snapshot != previous:
            print(f"{snapshot[0]} {snapshot[1]}% {snapshot[2]}", flush=True)
            previous = snapshot
        if snapshot[0] not in active_states:
            return row
        time.sleep(1)
    raise TimeoutError("The managed pose operation did not finish before its deadline")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify Vanta's managed local pose stack"
    )
    parser.add_argument(
        "action",
        choices=[
            "status",
            "install-component",
            "install-model",
            "install-identity-component",
            "install-identity-model",
            "extract",
            "generate",
            "generate-identity-pose",
        ],
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ["APPDATA"]) / "studio.vanta.desktop",
    )
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--pose-id")
    args = parser.parse_args()

    settings = settings_for(args.data_dir.resolve())
    settings.ensure_runtime_paths()
    db = Database(
        settings.database_path, settings.migrations_dir, settings.starter_presets_path
    )
    db.migrate()
    engine = EngineService(db, settings)
    try:
        if args.action == "install-component":
            engine.component_action("pose-control", "install")
            row = wait_for_row(
                db,
                "SELECT * FROM engine_components WHERE id=?",
                ("pose-control",),
                {"installing", "verifying", "starting", "restarting"},
                args.timeout,
            )
            success = row["state"] == "ready"
        elif args.action == "install-model":
            row = engine._pack_row(POSE_PACK_ALIAS)
            engine.pack_action(row["id"], "install")
            row = wait_for_row(
                db,
                "SELECT * FROM model_packs WHERE id=?",
                (str(row["id"]),),
                {"installing", "verifying"},
                args.timeout,
            )
            success = row["state"] == "ready" and bool(row["verified"])
        elif args.action == "install-identity-component":
            engine.component_action("identity-lock", "install")
            row = wait_for_row(
                db,
                "SELECT * FROM engine_components WHERE id=?",
                ("identity-lock",),
                {"installing", "verifying", "starting", "restarting"},
                args.timeout,
            )
            success = row["state"] == "ready"
        elif args.action == "install-identity-model":
            row = engine._pack_row(IDENTITY_PACK_ALIAS)
            engine.pack_action(row["id"], "install")
            row = wait_for_row(
                db,
                "SELECT * FROM model_packs WHERE id=?",
                (str(row["id"]),),
                {"installing", "verifying"},
                args.timeout,
            )
            success = row["state"] == "ready" and bool(row["verified"])
        elif args.action == "extract":
            if args.source is None:
                parser.error("extract requires --source")
            poses = PoseService(db, settings, engine)
            for existing in poses.list():
                if (
                    existing["name"] == "Essential V1 real DWPose evidence"
                    and existing["status"] == "failed"
                ):
                    poses.delete(existing["id"])
            row = poses.import_pose(
                PoseImportInput(
                    name="Essential V1 real DWPose evidence",
                    source_path=str(args.source.resolve()),
                    tags=["essential-v1", "real-extraction"],
                    favorite=True,
                    notes="Extracted locally from an existing Vanta-generated, user-owned image.",
                    strength=0.8,
                )
            )
            deadline = time.monotonic() + args.timeout
            while (
                row["status"] not in {"ready", "failed"} and time.monotonic() < deadline
            ):
                print(f"{row['status']} {row['progress']}%", flush=True)
                time.sleep(1)
                row = poses.get(row["id"])
            success = row["status"] == "ready"
        elif args.action == "generate":
            if not args.pose_id:
                parser.error("generate requires --pose-id")
            generations = GenerationService(db, engine)
            row = generations.queue(
                {
                    "operation": "generate",
                    "character_id": None,
                    "recipe_id": None,
                    "character_identity": "an original adult woman with dark hair",
                    "wardrobe": "editorial black jacket and tailored trousers",
                    "expression": "calm confident expression",
                    "pose": "use the selected structural pose",
                    "location": "restrained dark editorial studio",
                    "lighting": "soft directional key light, controlled shadows",
                    "camera": "full-body fashion photograph, 50mm lens",
                    "quality": "premium realistic detail, natural skin texture",
                    "direction": "original fictional character, composed editorial portrait",
                    "custom_tags": ["essential-v1", "pose-evidence"],
                    "negative_prompt": "low quality, malformed anatomy, extra limbs, text, watermark",
                    "model_alias": "photoreal_balanced",
                    "seed": 14072026,
                    "width": 768,
                    "height": 1024,
                    "steps": 25,
                    "guidance": 5.5,
                    "lora_ids": [],
                    "source_generation_id": None,
                    "identity_reference_id": None,
                    "pose_id": args.pose_id,
                    "pose_strength": 0.8,
                    "variation_strength": 0.45,
                    "upscale_profile": None,
                }
            )
            deadline = time.monotonic() + args.timeout
            while (
                row["status"] not in {"completed", "failed", "cancelled"}
                and time.monotonic() < deadline
            ):
                print(f"{row['status']} {row['progress']}%", flush=True)
                time.sleep(1)
                row = generations.get(row["id"])
            success = row["status"] == "completed"
            if success:
                row = {
                    "job": row,
                    "generation": db.query_one(
                        "SELECT * FROM generations ORDER BY created_at DESC LIMIT 1"
                    ),
                }
        elif args.action == "generate-identity-pose":
            if not args.pose_id or args.source is None:
                parser.error("generate-identity-pose requires --pose-id and --source")
            characters = CharacterRepository(db)
            character = next(
                (
                    item
                    for item in characters.list()
                    if item["name"] == "Essential V1 Identity Evidence"
                ),
                None,
            )
            if character is None:
                character = characters.create(
                    CharacterInput(
                        name="Essential V1 Identity Evidence",
                        identity_description=(
                            "Original adult woman with long straight black hair, pale skin, "
                            "blue-green eyes, dark defined brows, and an oval face."
                        ),
                    )
                )
            references = ReferenceRepository(db, settings.reference_root)
            if character["references"]:
                reference = character["references"][0]
            else:
                reference = references.import_image(
                    character["id"],
                    str(args.source.resolve()),
                    "Existing user-owned Vanta generation used for real identity+pose evidence.",
                )
            generations = GenerationService(db, engine)
            row = generations.queue(
                {
                    "operation": "generate",
                    "character_id": character["id"],
                    "recipe_id": None,
                    "character_identity": character["identity_description"],
                    "wardrobe": "editorial black jacket and tailored trousers",
                    "expression": "calm confident expression",
                    "pose": "use the selected structural pose",
                    "location": "restrained dark editorial studio",
                    "lighting": "soft directional key light, controlled shadows",
                    "camera": "full-body fashion photograph, 50mm lens",
                    "quality": "premium realistic detail, natural skin texture",
                    "direction": "preserve the original fictional character identity",
                    "custom_tags": ["essential-v1", "identity-pose-evidence"],
                    "negative_prompt": "low quality, malformed anatomy, extra limbs, text, watermark",
                    "model_alias": "photoreal_balanced",
                    "seed": 14072027,
                    "width": 768,
                    "height": 1024,
                    "steps": 25,
                    "guidance": 5.5,
                    "lora_ids": [],
                    "source_generation_id": None,
                    "identity_reference_id": reference["id"],
                    "pose_id": args.pose_id,
                    "pose_strength": 0.75,
                    "variation_strength": 0.45,
                    "upscale_profile": None,
                }
            )
            deadline = time.monotonic() + args.timeout
            while (
                row["status"] not in {"completed", "failed", "cancelled"}
                and time.monotonic() < deadline
            ):
                print(f"{row['status']} {row['progress']}%", flush=True)
                time.sleep(1)
                row = generations.get(row["id"])
            success = row["status"] == "completed"
            if success:
                row = {
                    "job": row,
                    "character": character,
                    "reference": reference,
                    "generation": db.query_one(
                        "SELECT * FROM generations ORDER BY created_at DESC LIMIT 1"
                    ),
                }
        else:
            row = {
                "component": db.query_one(
                    "SELECT * FROM engine_components WHERE id='pose-control'"
                ),
                "model": engine._pack_row(POSE_PACK_ALIAS),
                "identity_component": db.query_one(
                    "SELECT * FROM engine_components WHERE id='identity-lock'"
                ),
                "identity_model": engine._pack_row(IDENTITY_PACK_ALIAS),
                "latest_jobs": db.query_all(
                    "SELECT id, status, progress, error_message, created_at, "
                    "updated_at, completed_at FROM generation_jobs "
                    "ORDER BY created_at DESC LIMIT 5"
                ),
                "latest_generations": db.query_all(
                    "SELECT id, character_id, image_path, metadata, created_at "
                    "FROM generations ORDER BY created_at DESC LIMIT 5"
                ),
            }
            success = True
        print(json.dumps(row, indent=2, default=str), flush=True)
        return 0 if success else 1
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
