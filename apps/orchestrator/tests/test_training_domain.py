from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from vanta_orchestrator.config import Settings
from vanta_orchestrator.database import Database, utc_now
from vanta_orchestrator.engine import EngineService
from vanta_orchestrator.repositories import LoraRepository
from vanta_orchestrator.training import TrainingService


def test_checkpoint_epoch_cancel_and_resume_state(tmp_path: Path, monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[3]
    settings = Settings(data_dir=tmp_path, project_root=project_root)
    settings.ensure_runtime_paths()
    db = Database(settings.database_path, settings.migrations_dir, settings.starter_presets_path)
    db.migrate()
    service = TrainingService(
        db,
        settings,
        EngineService(db, settings),
        LoraRepository(db, settings.lora_root),
    )
    now = utc_now()
    dataset_id, run_id = "dataset-domain", "training-run-domain-a350"
    db.execute(
        "INSERT INTO training_datasets(id,name,trigger_token,model_alias,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (dataset_id, "Owned set", "vantaSubject", "photoreal_balanced", "", now, now),
    )
    output = settings.training_run_root / run_id / "output"
    sample_dir = output / "sample"
    sample_dir.mkdir(parents=True)
    checkpoint = output / "vantaSubject-vanta-a350.safetensors"
    checkpoint.write_bytes(b"real-checkpoint-shape")
    sample = sample_dir / "vantaSubject_e000001_00.png"
    Image.new("RGB", (64, 64), "plum").save(sample)
    db.execute(
        """INSERT INTO training_runs(
            id,dataset_id,profile,status,progress,current_epoch,total_epochs,current_step,
            total_steps,model_alias,output_name,output_dir,parameters,estimates,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            dataset_id,
            "safe_12gb",
            "training",
            50,
            0,
            1,
            6,
            12,
            "photoreal_balanced",
            "vantaSubject-vanta-a350",
            str(output),
            json.dumps({}),
            json.dumps({}),
            now,
            now,
        ),
    )

    service._scan_checkpoints(run_id)
    scanned = service.get_run(run_id)
    assert scanned["checkpoints"][0]["epoch"] == 1
    assert scanned["checkpoints"][0]["validation_sample_path"] == str(sample)

    cancelled = service.cancel_run(run_id)
    assert cancelled["status"] == "cancelling"
    assert cancelled["cancellation_requested"] is True

    resume_state = output / "vantaSubject-state"
    resume_state.mkdir()
    db.execute(
        "UPDATE training_runs SET status='cancelled',resume_state_path=? WHERE id=?",
        (str(resume_state), run_id),
    )
    monkeypatch.setattr(service, "_execute_run", lambda _run_id, _resume: None)
    resumed = service.resume_run(run_id)
    assert resumed["status"] == "queued"
    assert resumed["cancellation_requested"] is False
    service.close()
