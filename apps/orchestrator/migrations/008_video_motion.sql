ALTER TABLE generations ADD COLUMN media_type TEXT NOT NULL DEFAULT 'image';

CREATE TABLE motion_assets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_path TEXT NOT NULL,
  preview_path TEXT,
  thumbnail_path TEXT,
  start_seconds REAL NOT NULL DEFAULT 0,
  end_seconds REAL NOT NULL,
  fit_mode TEXT NOT NULL DEFAULT 'crop' CHECK (fit_mode IN ('crop', 'fit')),
  smoothing REAL NOT NULL DEFAULT 0.5 CHECK (smoothing >= 0 AND smoothing <= 1),
  strength REAL NOT NULL DEFAULT 0.65 CHECK (strength >= 0 AND strength <= 1),
  status TEXT NOT NULL DEFAULT 'queued',
  progress INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_motion_assets_created ON motion_assets(created_at DESC);
