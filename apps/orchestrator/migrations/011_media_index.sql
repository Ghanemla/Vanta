PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS media_assets (
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  variant TEXT NOT NULL,
  path TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  file_size INTEGER NOT NULL,
  width INTEGER,
  height INTEGER,
  duration_seconds REAL,
  state TEXT NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'missing', 'unsafe', 'invalid')),
  verified_at TEXT NOT NULL,
  PRIMARY KEY (entity_type, entity_id, variant)
);

CREATE INDEX IF NOT EXISTS idx_media_assets_state
  ON media_assets(state, entity_type, entity_id);
