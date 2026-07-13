from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from vanta_orchestrator.config import Settings
from vanta_orchestrator.database import Database
from vanta_orchestrator.engine import EngineService, GenerationService
from vanta_orchestrator.repositories import CharacterRepository, LoraRepository
from vanta_orchestrator.schemas import (
    CharacterInput,
    TrainingDatasetInput,
    TrainingInstallInput,
    TrainingRunInput,
)
from vanta_orchestrator.training import ACTIVE_STATES, TrainingService


def settings_for(data_dir: Path) -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    return Settings(
        data_dir=data_dir,
        project_root=project_root,
        resource_root=project_root,
        logs_dir=data_dir / "logs",
    )


def wait_component(db: Database, item_id: str, timeout: int) -> dict:
    deadline, previous = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        row = db.query_one("SELECT * FROM engine_components WHERE id=?", (item_id,))
        if not row:
            raise RuntimeError("Component record disappeared")
        snapshot = (row["state"], row["progress"], row["last_health_message"])
        if snapshot != previous:
            print(f"{snapshot[0]} {snapshot[1]}% {snapshot[2]}", flush=True)
            previous = snapshot
        if row["state"] != "installing":
            return row
        time.sleep(1)
    raise TimeoutError(f"{item_id} did not finish before its deadline")


def wait_run(training: TrainingService, run_id: str, timeout: int) -> dict:
    deadline, previous = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        row = training.get_run(run_id)
        snapshot = (
            row["status"],
            row["progress"],
            row["current_epoch"],
            row["current_step"],
            row["error_message"],
        )
        if snapshot != previous:
            print(
                f"{snapshot[0]} {snapshot[1]}% epoch={snapshot[2]}/{row['total_epochs']} "
                f"step={snapshot[3]}/{row['total_steps']} {snapshot[4] or ''}",
                flush=True,
            )
            previous = snapshot
        if row["status"] not in ACTIVE_STATES:
            return row
        time.sleep(2)
    raise TimeoutError("Training did not finish before its deadline")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify Vanta's managed local LoRA training"
    )
    parser.add_argument(
        "action",
        choices=[
            "status",
            "install-trainer",
            "install-captioning",
            "create-evidence-dataset",
            "caption",
            "train",
            "install-lora",
            "generate",
        ],
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ["APPDATA"]) / "studio.vanta.desktop",
    )
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--source", action="append", type=Path, default=[])
    parser.add_argument("--dataset-id")
    parser.add_argument("--run-id")
    parser.add_argument("--epochs", type=int, default=1)
    args = parser.parse_args()

    settings = settings_for(args.data_dir.resolve())
    settings.ensure_runtime_paths()
    db = Database(
        settings.database_path, settings.migrations_dir, settings.starter_presets_path
    )
    db.migrate()
    engine = EngineService(db, settings)
    loras = LoraRepository(db, settings.lora_root)
    training = TrainingService(db, settings, engine, loras)
    try:
        if args.action in {"install-trainer", "install-captioning"}:
            item_id = (
                "lora-training" if args.action == "install-trainer" else "captioning"
            )
            training.component_action(item_id, "install")
            row = wait_component(db, item_id, args.timeout)
            success = row["state"] == "ready"
        elif args.action == "create-evidence-dataset":
            if not args.source:
                parser.error(
                    "create-evidence-dataset requires one or more --source images"
                )
            characters = CharacterRepository(db)
            character = next(
                (
                    item
                    for item in characters.list(True)
                    if item["name"] == "Essential V1 Trained Character"
                ),
                None,
            )
            if character is None:
                character = characters.create(
                    CharacterInput(
                        name="Essential V1 Trained Character",
                        identity_description=(
                            "vantaTrainSubject, an original fictional adult woman with short dark hair "
                            "and a composed editorial presence"
                        ),
                        style_notes="Locally trained Essential V1 evidence character.",
                    )
                )
            dataset = next(
                (
                    item
                    for item in training.list_datasets()
                    if item["name"] == "Essential V1 real training evidence"
                ),
                None,
            )
            if dataset is None:
                dataset = training.create_dataset(
                    TrainingDatasetInput(
                        name="Essential V1 real training evidence",
                        character_id=character["id"],
                        trigger_token="vantaTrainSubject",
                        notes="Owned Vanta synthetic source images used for local integration evidence.",
                    )
                )
            if not dataset["images"]:
                training.import_images(
                    dataset["id"], [str(path.resolve()) for path in args.source]
                )
            row = training.caption_dataset(dataset["id"])
            success = bool(row["images"]) and all(
                image["caption"] for image in row["images"]
            )
        elif args.action == "caption":
            if not args.dataset_id:
                parser.error("caption requires --dataset-id")
            row = training.caption_dataset(args.dataset_id)
            success = bool(row["images"]) and all(
                image["caption"] for image in row["images"]
            )
        elif args.action == "train":
            if not args.dataset_id:
                parser.error("train requires --dataset-id")
            row = training.start_run(
                TrainingRunInput(
                    dataset_id=args.dataset_id,
                    profile="safe_12gb",
                    epochs=args.epochs,
                    validation_prompt=(
                        "portrait photograph of vantaTrainSubject, structured plum coat, "
                        "neutral charcoal studio, soft editorial light"
                    ),
                )
            )
            row = wait_run(training, row["id"], args.timeout)
            success = row["status"] == "completed" and bool(row["checkpoints"])
        elif args.action == "install-lora":
            if not args.run_id:
                parser.error("install-lora requires --run-id")
            run = training.get_run(args.run_id)
            dataset = training.get_dataset(run["dataset_id"])
            checkpoint = next(
                (item for item in run["checkpoints"] if item["selected"]),
                run["checkpoints"][-1] if run["checkpoints"] else None,
            )
            if checkpoint is None or dataset["character_id"] is None:
                raise RuntimeError(
                    "The completed run has no installable checkpoint or character"
                )
            row = training.install_checkpoint(
                run["id"],
                TrainingInstallInput(
                    checkpoint_id=checkpoint["id"],
                    name="Essential V1 locally trained identity",
                    character_id=dataset["character_id"],
                    strength=0.75,
                ),
            )
            success = bool(row["run"]["installed_lora_id"])
        elif args.action == "generate":
            if not args.run_id:
                parser.error("generate requires --run-id")
            run = training.get_run(args.run_id)
            dataset = training.get_dataset(run["dataset_id"])
            if not run["installed_lora_id"] or not dataset["character_id"]:
                raise RuntimeError("Install the trained LoRA into its character first")
            generations = GenerationService(db, engine)
            row = generations.queue(
                {
                    "operation": "generate",
                    "character_id": dataset["character_id"],
                    "recipe_id": None,
                    "character_identity": (
                        "vantaTrainSubject, original fictional adult woman with short dark hair"
                    ),
                    "wardrobe": "structured deep-plum wool coat and black silk blouse",
                    "expression": "calm self-possessed expression",
                    "pose": "standing three-quarter editorial portrait",
                    "location": "restrained charcoal studio",
                    "lighting": "large softbox key light with a subtle warm rim",
                    "camera": "85mm portrait photograph, shallow depth of field",
                    "quality": "premium realistic detail, natural skin texture",
                    "direction": "original fictional character, authored campaign frame",
                    "custom_tags": ["essential-v1", "trained-lora-evidence"],
                    "negative_prompt": "text, watermark, malformed anatomy, plastic skin",
                    "model_alias": "photoreal_balanced",
                    "seed": 14072034,
                    "width": 512,
                    "height": 768,
                    "steps": 16,
                    "guidance": 5.5,
                    "lora_ids": [run["installed_lora_id"]],
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
                "trainer": db.query_one(
                    "SELECT * FROM engine_components WHERE id='lora-training'"
                ),
                "captioning": db.query_one(
                    "SELECT * FROM engine_components WHERE id='captioning'"
                ),
                "datasets": training.list_datasets(),
                "runs": training.list_runs()[:5],
            }
            success = True
        print(json.dumps(row, indent=2, default=str), flush=True)
        return 0 if success else 1
    finally:
        training.close()
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
