ALTER TABLE installation_jobs ADD COLUMN partial_path TEXT;
ALTER TABLE installation_jobs ADD COLUMN paused_requested INTEGER NOT NULL DEFAULT 0;
ALTER TABLE installation_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE installation_jobs ADD COLUMN completed_at TEXT;
ALTER TABLE installation_jobs ADD COLUMN error_message TEXT;
ALTER TABLE installation_jobs ADD COLUMN process_id INTEGER;
ALTER TABLE installation_jobs ADD COLUMN worker_heartbeat TEXT;
ALTER TABLE installation_jobs ADD COLUMN verified_file_hash TEXT;
ALTER TABLE installation_jobs ADD COLUMN health_check_result TEXT;

CREATE INDEX IF NOT EXISTS idx_installation_jobs_state_updated
  ON installation_jobs(state, updated_at DESC);
