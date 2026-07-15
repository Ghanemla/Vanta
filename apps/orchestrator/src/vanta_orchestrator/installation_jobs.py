from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import Database, utc_now

ACTIVE_STATES = {
    "queued",
    "validating_storage",
    "checking_storage",
    "waiting_for_dependency",
    "connecting",
    "downloading",
    "paused",
    "verifying_download",
    "extracting",
    "installing",
    "activating",
    "verifying_installation",
    "starting_service",
    "health_checking",
    "cancelling",
    "removing",
}
TERMINAL_STATES = {"ready", "cancelled", "failed", "repair_needed", "removed"}


def _safe(value: str) -> str:
    """Keep endpoint URLs and user messages useful without retaining credentials."""
    return re.sub(
        r"([?&](?:token|key|authorization|signature|policy|credential)=)[^&\s]+",
        r"\1[redacted]",
        value,
        flags=re.IGNORECASE,
    )[:2000]


def _elapsed(started_at: str | None) -> float:
    if not started_at:
        return 0.0
    try:
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - started).total_seconds())
    except ValueError:
        return 0.0


class InstallationJobs:
    """Authoritative durable state for managed downloads, installs, repairs and removals."""

    def __init__(self, db: Database):
        self.db = db
        self._speed_samples: dict[str, tuple[float, int]] = {}

    def start(
        self,
        component_id: str,
        operation: str,
        *,
        source: str | None = None,
        destination: Path | None = None,
        partial_path: Path | None = None,
        total_bytes: int | None = None,
        resumable: bool = False,
    ) -> str:
        active = self.db.query_one(
            f"SELECT id FROM installation_jobs WHERE component_id=? AND state IN "
            f"({','.join('?' for _ in ACTIVE_STATES)}) ORDER BY updated_at DESC LIMIT 1",
            (component_id, *sorted(ACTIVE_STATES)),
        )
        if active:
            return str(active["id"])
        previous = self.db.query_one(
            "SELECT retry_count FROM installation_jobs WHERE component_id=? ORDER BY updated_at DESC LIMIT 1",
            (component_id,),
        )
        retry_count = int(previous["retry_count"] or 0) + 1 if previous else 0
        job_id, now = f"install-{uuid.uuid4().hex}", utc_now()
        self._speed_samples[job_id] = (time.monotonic(), 0)
        self.db.execute(
            """INSERT INTO installation_jobs
            (id,component_id,operation,state,stage,source,destination,partial_path,total_bytes,
             resumable,retry_count,summary,created_at,started_at,updated_at,worker_heartbeat)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id,
                component_id,
                operation,
                "queued",
                "Queued",
                _safe(source or ""),
                str(destination) if destination else None,
                str(partial_path) if partial_path else None,
                total_bytes,
                int(resumable),
                retry_count,
                "Waiting to begin",
                now,
                now,
                now,
                now,
            ),
        )
        return job_id

    def update(self, job_id: str, state: str, stage: str, summary: str, **values: Any) -> None:
        existing = self.db.query_one("SELECT * FROM installation_jobs WHERE id=?", (job_id,))
        if existing is None:
            raise KeyError(job_id)
        downloaded = int(values.get("downloaded_bytes", existing["downloaded_bytes"]) or 0)
        total = values.get("total_bytes", existing["total_bytes"])
        percentage = values.get("percentage")
        if percentage is None and total:
            percentage = min(100, int(downloaded * 100 / int(total)))
        speed = values.get("speed_bytes_per_second")
        now_mono = time.monotonic()
        if speed is None:
            prior_time, prior_bytes = self._speed_samples.get(job_id, (now_mono, downloaded))
            sample_time = now_mono - prior_time
            sample_bytes = downloaded - prior_bytes
            if sample_time >= 0.25 and sample_bytes >= 0:
                speed = sample_bytes / sample_time
                self._speed_samples[job_id] = (now_mono, downloaded)
            else:
                speed = existing["speed_bytes_per_second"]
        eta = values.get("eta_seconds")
        if eta is None and speed and total and downloaded < int(total):
            eta = (int(total) - downloaded) / float(speed)
        completed = utc_now() if state in TERMINAL_STATES else None
        cancellation = values.get("cancellation_requested", existing["cancellation_requested"])
        paused = values.get("paused_requested", existing["paused_requested"])
        error_message = values.get("error_message", existing["error_message"])
        technical = values.get("technical_details", existing["technical_details"])
        now = utc_now()
        self.db.execute(
            """UPDATE installation_jobs SET state=?,stage=?,summary=?,downloaded_bytes=?,
            total_bytes=COALESCE(?,total_bytes),extracted_bytes=COALESCE(?,extracted_bytes),
            percentage=COALESCE(?,percentage),speed_bytes_per_second=?,elapsed_seconds=?,
            eta_seconds=?,resumable=?,cancellation_requested=?,paused_requested=?,error_category=?,
            error_message=?,technical_details=?,process_id=COALESCE(?,process_id),
            worker_heartbeat=?,verified_file_hash=COALESCE(?,verified_file_hash),
            health_check_result=COALESCE(?,health_check_result),retry_count=?,completed_at=?,updated_at=? WHERE id=?""",
            (
                state,
                stage,
                _safe(summary),
                downloaded,
                total,
                values.get("extracted_bytes"),
                percentage,
                speed,
                values.get("elapsed_seconds", _elapsed(existing["started_at"])),
                eta,
                int(values.get("resumable", existing["resumable"])),
                int(bool(cancellation)),
                int(bool(paused)),
                values.get("error_category", existing["error_category"]),
                _safe(str(error_message or "")) or None,
                _safe(str(technical or "")),
                values.get("process_id"),
                now,
                values.get("verified_file_hash"),
                values.get("health_check_result"),
                values.get("retry_count", existing["retry_count"]),
                completed,
                now,
                job_id,
            ),
        )

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["state"] not in ACTIVE_STATES:
            raise ValueError("This installation job is no longer active")
        self.update(
            job_id,
            "cancelling",
            "Cancelling",
            "Stopping network reads and preserving verified partial bytes",
            cancellation_requested=True,
            paused_requested=False,
        )
        return self.get(job_id)

    def request_pause(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["state"] not in {"connecting", "downloading"}:
            raise ValueError("Only an active download can be paused")
        self.update(
            job_id,
            "paused",
            "Paused",
            "Transfer paused; partial bytes are preserved",
            paused_requested=True,
        )
        return self.get(job_id)

    def request_resume(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["state"] not in {"paused", "repair_needed", "failed", "cancelled"}:
            raise ValueError("This installation job cannot be resumed")
        self.update(
            job_id,
            "queued",
            "Queued to resume",
            "Resume will validate the partial file before requesting the remaining range",
            paused_requested=False,
            cancellation_requested=False,
            error_category=None,
            error_message=None,
            technical_details="",
            retry_count=int(job.get("retry_count") or 0) + 1,
        )
        return self.get(job_id)

    def reset_failed(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["state"] not in {"failed", "repair_needed", "cancelled"}:
            raise ValueError("Only a failed, cancelled, or repair-needed job can be reset")
        self.update(
            job_id,
            "removed",
            "Failure dismissed",
            "The stale job was dismissed; component readiness will still be derived from real files",
            paused_requested=False,
            cancellation_requested=False,
        )
        return self.get(job_id)

    def cancel_requested(self, job_id: str) -> bool:
        row = self.db.query_one(
            "SELECT cancellation_requested FROM installation_jobs WHERE id=?", (job_id,)
        )
        return bool(row and row["cancellation_requested"])

    def pause_requested(self, job_id: str) -> bool:
        row = self.db.query_one(
            "SELECT paused_requested FROM installation_jobs WHERE id=?", (job_id,)
        )
        return bool(row and row["paused_requested"])

    def get(self, job_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM installation_jobs WHERE id=?", (job_id,))
        if row is None:
            raise KeyError(job_id)
        return self._present(row)

    def current(self, component_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM installation_jobs WHERE component_id=? ORDER BY updated_at DESC LIMIT 1",
            (component_id,),
        )
        return self._present(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        return [
            self._present(row)
            for row in self.db.query_all("SELECT * FROM installation_jobs ORDER BY updated_at DESC")
        ]

    @staticmethod
    def _present(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for field in ("resumable", "cancellation_requested", "paused_requested"):
            result[field] = bool(result.get(field))
        return result

    def recover(self) -> None:
        for row in self.db.query_all(
            f"SELECT * FROM installation_jobs WHERE state IN ({','.join('?' for _ in ACTIVE_STATES)})",
            tuple(sorted(ACTIVE_STATES)),
        ):
            partial = Path(row["partial_path"]) if row.get("partial_path") else None
            actual = partial.stat().st_size if partial and partial.is_file() else 0
            if row.get("cancellation_requested"):
                self.update(
                    str(row["id"]),
                    "cancelled",
                    "Cancelled",
                    "The interrupted cancellation was reconciled; partial bytes remain available for retry",
                    downloaded_bytes=actual,
                    paused_requested=False,
                    error_category="cancelled",
                    error_message="The installation was cancelled before Vanta closed.",
                )
                continue
            if row["state"] in {"connecting", "downloading", "paused", "queued"} and actual:
                self.update(
                    str(row["id"]),
                    "paused",
                    "Interrupted; ready to resume",
                    "Vanta reconciled the persisted job with the partial file on disk",
                    downloaded_bytes=actual,
                    paused_requested=True,
                    resumable=True,
                    error_category="interrupted",
                    error_message="The previous transfer stopped when Vanta closed. Resume to continue.",
                )
            else:
                self.update(
                    str(row["id"]),
                    "repair_needed",
                    "Recovery required",
                    "Vanta closed before this installation stage completed; real files must be verified before retrying",
                    downloaded_bytes=actual,
                    error_category="interrupted",
                    error_message="The installation was interrupted. Verify or repair it before use.",
                )
