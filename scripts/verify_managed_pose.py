from __future__ import annotations

import argparse
import base64
import io
import json
import os
import time
from pathlib import Path

from PIL import Image, ImageDraw

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
            "inpaint",
            "variation-clothing",
            "variation-lighting",
            "import-flux",
            "generate-flux",
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
    parser.add_argument("--source-generation-id")
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
        elif args.action == "inpaint":
            if not args.source_generation_id:
                parser.error("inpaint requires --source-generation-id")
            source = db.query_one(
                "SELECT * FROM generations WHERE id=?", (args.source_generation_id,)
            )
            if source is None or not Path(source["image_path"]).is_file():
                raise RuntimeError("The requested source generation is unavailable")
            with Image.open(source["image_path"]) as original:
                width, height = original.size
            mask = Image.new("L", (width, height), 0)
            draw = ImageDraw.Draw(mask)
            draw.rounded_rectangle(
                (
                    round(width * 0.27),
                    round(height * 0.31),
                    round(width * 0.73),
                    round(height * 0.64),
                ),
                radius=max(12, round(width * 0.08)),
                fill=255,
            )
            encoded = io.BytesIO()
            mask.save(encoded, "PNG")
            generations = GenerationService(db, engine)
            row = generations.queue(
                {
                    "operation": "inpaint",
                    "character_id": source.get("character_id"),
                    "recipe_id": source.get("recipe_id"),
                    "character_identity": "",
                    "wardrobe": "",
                    "expression": "",
                    "pose": "",
                    "location": "",
                    "lighting": "",
                    "camera": "",
                    "quality": "",
                    "direction": "cream satin blouse",
                    "custom_tags": ["essential-v1", "inpaint-evidence"],
                    "negative_prompt": "",
                    "model_alias": source["model_alias"],
                    "seed": 14072028,
                    "width": 512,
                    "height": 512,
                    "steps": 25,
                    "guidance": 5.5,
                    "lora_ids": [],
                    "source_generation_id": source["id"],
                    "identity_reference_id": None,
                    "pose_id": None,
                    "pose_strength": None,
                    "variation_strength": 0.45,
                    "variation_mode": "general",
                    "variation_prompt": "",
                    "region_prompt": (
                        "front view fitted cream satin blouse worn by the woman, elegant "
                        "off-shoulder neckline, realistic fabric folds, premium editorial styling"
                    ),
                    "region_negative_prompt": (
                        "text, watermark, malformed fabric, extra limbs, distorted anatomy"
                    ),
                    "inpaint_mask_data_url": "data:image/png;base64,"
                    + base64.b64encode(encoded.getvalue()).decode(),
                    "inpaint_strength": 0.55,
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
                        "SELECT * FROM generations WHERE id=?",
                        (row["result_generation_id"],),
                    ),
                }
        elif args.action in {"variation-clothing", "variation-lighting"}:
            if not args.source_generation_id:
                parser.error(f"{args.action} requires --source-generation-id")
            source = db.query_one(
                "SELECT * FROM generations WHERE id=?", (args.source_generation_id,)
            )
            if source is None:
                raise RuntimeError("The requested source generation is unavailable")
            source_metadata = json.loads(source["metadata"])
            request = dict(source_metadata.get("request") or {})
            mode = "clothing" if args.action == "variation-clothing" else "lighting"
            change = (
                "bright crimson red tailored blazer over a black silk top, premium wool fabric"
                if mode == "clothing"
                else "warm rose-gold side light, cinematic controlled shadows, subtle rim light"
            )
            request.update(
                {
                    "operation": "generate",
                    "source_generation_id": source["id"],
                    "variation_mode": mode,
                    "variation_prompt": change,
                    "variation_strength": 0.62 if mode == "clothing" else 0.38,
                    "wardrobe": change
                    if mode == "clothing"
                    else request.get("wardrobe", ""),
                    "lighting": change
                    if mode == "lighting"
                    else request.get("lighting", ""),
                    "seed": 14072029 if mode == "clothing" else 14072030,
                    "width": source["width"],
                    "height": source["height"],
                    "steps": 25,
                    "guidance": 5.5,
                    "lora_ids": [],
                    "identity_reference_id": None,
                    "pose_id": None,
                    "pose_strength": None,
                    "inpaint_mask_data_url": None,
                    "region_prompt": "",
                    "region_negative_prompt": "",
                    "inpaint_strength": 0.62,
                    "upscale_profile": None,
                }
            )
            generations = GenerationService(db, engine)
            row = generations.queue(request)
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
                        "SELECT * FROM generations WHERE id=?",
                        (row["result_generation_id"],),
                    ),
                }
        elif args.action == "import-flux":
            if args.source is None:
                parser.error("import-flux requires --source")
            row = engine.import_model(
                str(args.source.resolve()),
                "photoreal_max",
                "User-owned local self-contained FLUX.1-dev FP8 checkpoint; license acceptance recorded for local use.",
            )
            success = row["state"] == "ready" and bool(row["verified"])
        elif args.action == "generate-flux":
            generations = GenerationService(db, engine)
            row = generations.queue(
                {
                    "operation": "generate",
                    "character_id": None,
                    "recipe_id": None,
                    "character_identity": "an original adult woman with short dark hair",
                    "wardrobe": "structured deep-plum wool coat, black silk blouse",
                    "expression": "calm self-possessed expression",
                    "pose": "standing three-quarter portrait",
                    "location": "restrained charcoal editorial studio",
                    "lighting": "large softbox key light, subtle warm rim light, controlled shadows",
                    "camera": "medium-format editorial photograph, 80mm lens, shallow depth of field",
                    "quality": "premium photoreal detail, natural skin texture, tactile fabric",
                    "direction": "original fictional character, authored fashion campaign frame",
                    "custom_tags": ["essential-v1", "flux-evidence"],
                    "negative_prompt": "text, watermark, malformed anatomy, plastic skin",
                    "model_alias": "photoreal_max",
                    "seed": 14072031,
                    "width": 768,
                    "height": 1024,
                    "steps": 20,
                    "guidance": 3.5,
                    "lora_ids": [],
                    "source_generation_id": None,
                    "identity_reference_id": None,
                    "pose_id": None,
                    "pose_strength": None,
                    "variation_strength": 0.45,
                    "variation_mode": "general",
                    "variation_prompt": "",
                    "inpaint_mask_data_url": None,
                    "region_prompt": "",
                    "region_negative_prompt": "",
                    "inpaint_strength": 0.62,
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
                        "SELECT * FROM generations WHERE id=?",
                        (row["result_generation_id"],),
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
                "image_editing_component": db.query_one(
                    "SELECT * FROM engine_components WHERE id='image-finishing'"
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
