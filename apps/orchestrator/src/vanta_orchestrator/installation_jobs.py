from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

from .database import Database, utc_now

ACTIVE_STATES = {
    "queued",
    "checking_storage",
    "waiting_for_dependency",
    "connecting",
    "downloading",
    "paused",
    "verifying_download",
    "extracting",
    "installing",
    "verifying_installation",
    "starting_service",
    "health_checking",
    "cancelling",
}


def _safe(value: str) -> str:
    """Keep endpoint URLs and user messages useful without retaining credentials."""
    return re.sub(r"([?&](?:token|key|authorization)=)[^&\s]+", r"\1[redacted]", value)[:1200]


class InstallationJobs:
    """Durable, component-scoped install progress used by setup and Models & Engine."""

    def __init__(self, db: Database):
        self.db = db
        self._started: dict[str, float] = {}

    def start(
        self,
        component_id: str,
        operation: str,
        *,
        source: str | None = None,
        destination: Path | None = None,
        total_bytes: int | None = None,
        resumable: bool = False,
    ) -> str:
        active = self.db.query_one(
            "SELECT id FROM installation_jobs WHERE component_id=? AND state IN "
            "('queued','checking_storage','waiting_for_dependency','connecting','downloading',"
            "'paused','verifying_download','extracting','installing','verifying_installation',"
            "'starting_service','health_checking','cancelling') ORDER BY updated_at DESC LIMIT 1",
            (component_id,),
        )
        if active:
            return str(active["id"])
        job_id, now = f"install-{uuid.uuid4().hex}", utc_now()
        self._started[job_id] = time.monotonic()
        self.db.execute(
            """INSERT INTO installation_jobs
            (id,component_id,operation,state,stage,source,destination,total_bytes,resumable,summary,created_at,started_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id,
                component_id,
                operation,
                "queued",
                "Queued",
                _safe(source or ""),
                str(destination) if destination else None,
                total_bytes,
                int(resumable),
                "Waiting to begin",
                now,
                now,
                now,
            ),
        )
        return job_id

    def update(self, job_id: str, state: str, stage: str, summary: str, **values: Any) -> None:
        elapsed = max(0.0, time.monotonic() - self._started.setdefault(job_id, time.monotonic()))
        existing = self.db.query_one(
            "SELECT downloaded_bytes,total_bytes FROM installation_jobs WHERE id=?", (job_id,)
        ) or {"downloaded_bytes": 0, "total_bytes": None}
        downloaded = int(values.get("downloaded_bytes", existing["downloaded_bytes"]) or 0)
        total = values.get("total_bytes", existing["total_bytes"])
        speed = downloaded / elapsed if downloaded and elapsed else None
        eta = (
            ((int(total) - downloaded) / speed)
            if speed and total and downloaded < int(total)
            else None
        )
        percentage = values.get("percentage")
        if percentage is None and total:
            percentage = min(100, round(downloaded * 100 / int(total)))
        self.db.execute(
            """UPDATE installation_jobs SET state=?,stage=?,summary=?,downloaded_bytes=?,
            total_bytes=COALESCE(?,total_bytes),extracted_bytes=?,percentage=COALESCE(?,percentage),
            speed_bytes_per_second=?,elapsed_seconds=?,eta_seconds=?,cancellation_requested=?,
            error_category=?,technical_details=?,updated_at=? WHERE id=?""",
            (
                state,
                stage,
                _safe(summary),
                downloaded,
                total,
                values.get("extracted_bytes"),
                percentage,
                speed,
                elapsed,
                eta,
                int(values.get("cancellation_requested", False)),
                values.get("error_category"),
                _safe(str(values.get("technical_details", ""))),
                utc_now(),
                job_id,
            ),
        )

    def current(self, component_id: str) -> dict[str, Any] | None:
        return self.db.query_one(
            "SELECT * FROM installation_jobs WHERE component_id=? ORDER BY updated_at DESC LIMIT 1",
            (component_id,),
        )

    def list(self) -> list[dict[str, Any]]:
        return self.db.query_all("SELECT * FROM installation_jobs ORDER BY updated_at DESC")

    def recover(self) -> None:
        # A sidecar cannot safely resume an in-memory extraction/process. Downloads remain on disk
        # as .partial and are resumed on the next explicit action with Range where supported.
        self.db.execute(
            """UPDATE installation_jobs SET state='repair_needed',stage='Recovery required',
            summary='Vanta was closed while this installation was active. Review and retry; any verified partial download is preserved.',
            error_category='interrupted',updated_at=? WHERE state IN
            ('queued','checking_storage','waiting_for_dependency','connecting','downloading','paused',
            'verifying_download','extracting','installing','verifying_installation','starting_service','health_checking','cancelling')""",
            (utc_now(),),
        )
