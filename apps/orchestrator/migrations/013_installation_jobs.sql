CREATE TABLE IF NOT EXISTS installation_jobs (
  id TEXT PRIMARY KEY,
  component_id TEXT NOT NULL,
  operation TEXT NOT NULL,
  state TEXT NOT NULL,
  stage TEXT NOT NULL,
  source TEXT,
  destination TEXT,
  downloaded_bytes INTEGER NOT NULL DEFAULT 0,
  total_bytes INTEGER,
  extracted_bytes INTEGER,
  percentage INTEGER NOT NULL DEFAULT 0,
  speed_bytes_per_second REAL,
  elapsed_seconds REAL NOT NULL DEFAULT 0,
  eta_seconds REAL,
  resumable INTEGER NOT NULL DEFAULT 0,
  cancellation_requested INTEGER NOT NULL DEFAULT 0,
  error_category TEXT,
  summary TEXT NOT NULL DEFAULT '',
  technical_details TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  started_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_installation_jobs_component_updated
  ON installation_jobs(component_id, updated_at DESC);
