from __future__ import annotations

from pathlib import Path

from vanta_orchestrator.database import Database
from vanta_orchestrator.engine import ModelPackCollection
from vanta_orchestrator.installation_jobs import InstallationJobs


def make_jobs(tmp_path: Path) -> InstallationJobs:
    root = Path(__file__).resolve().parents[3]
    db = Database(
        tmp_path / "VantaAcceptance" / "vanta.db",
        root / "apps" / "orchestrator" / "migrations",
        root / "data" / "starter_presets.json",
    )
    db.migrate()
    return InstallationJobs(db)


def test_installation_job_persists_real_byte_progress_and_eta(tmp_path: Path):
    jobs = make_jobs(tmp_path)
    job_id = jobs.start(
        "workflow-runtime",
        "install",
        source="https://example.invalid/archive.7z?token=secret",
        destination=tmp_path / "engine" / "archive.7z.partial",
        total_bytes=1000,
        resumable=True,
    )
    jobs.update(
        job_id, "downloading", "Downloading", "Downloading reviewed runtime", downloaded_bytes=500
    )
    current = jobs.current("workflow-runtime")
    assert current and current["downloaded_bytes"] == 500
    assert current["total_bytes"] == 1000 and current["percentage"] == 50
    assert current["resumable"] == 1 and "secret" not in current["source"]
    assert current["worker_heartbeat"]


def test_active_installation_is_recovered_without_a_stale_installing_state(tmp_path: Path):
    jobs = make_jobs(tmp_path)
    job_id = jobs.start("workflow-runtime", "install")
    jobs.update(job_id, "extracting", "Extracting", "Extracting runtime")
    jobs.recover()
    current = jobs.current("workflow-runtime")
    assert current and current["state"] == "repair_needed"
    assert "closed" in current["summary"].lower()


def test_one_active_installation_per_component(tmp_path: Path):
    jobs = make_jobs(tmp_path)
    first = jobs.start("workflow-runtime", "install")
    assert jobs.start("workflow-runtime", "repair") == first


def test_cancelled_job_never_becomes_ready_and_retry_uses_the_partial_file(tmp_path: Path):
    jobs = make_jobs(tmp_path)
    partial = tmp_path / "engine.7z.partial"
    partial.write_bytes(b"partial-real-bytes")
    job_id = jobs.start(
        "workflow-runtime",
        "install",
        partial_path=partial,
        total_bytes=100,
        resumable=True,
    )
    jobs.update(job_id, "downloading", "Downloading", "Writing bytes", downloaded_bytes=18)
    jobs.request_cancel(job_id)
    jobs.recover()
    cancelled = jobs.get(job_id)
    assert cancelled["state"] == "cancelled"
    assert cancelled["downloaded_bytes"] == partial.stat().st_size
    assert cancelled["state"] != "ready"
    previous_retry = cancelled["retry_count"]
    resumed = jobs.request_resume(job_id)
    assert resumed["state"] == "queued"
    assert resumed["completed_at"] is None
    assert resumed["retry_count"] == previous_retry + 1


def test_realvisxl_manifest_is_immutable_and_selects_only_fp16():
    root = Path(__file__).resolve().parents[3]
    collection = ModelPackCollection.model_validate_json(
        (root / "engine" / "manifests" / "model-packs.v1.json").read_text(encoding="utf-8")
    )
    pack = next(item for item in collection.packs if item.alias == "photoreal_balanced")
    download = pack.download
    assert download["filename"] == "RealVisXL_V5.0_fp16.safetensors"
    assert len(download["source_revision"]) == 40
    assert download["bytes"] == 6938065488
    assert pack.sha256 == "6a35a7855770ae9820a3c931d4964c3817b6d9e3c6f9c4dabb5b3a94e5643b80"
    assert pack.license.name == "OpenRAIL++"
