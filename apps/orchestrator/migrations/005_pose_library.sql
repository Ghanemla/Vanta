CREATE TABLE IF NOT EXISTS pose_assets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'global' CHECK (scope IN ('global', 'character')),
  character_id TEXT REFERENCES characters(id) ON DELETE SET NULL,
  source_path TEXT NOT NULL,
  source_thumbnail_path TEXT NOT NULL,
  control_path TEXT NOT NULL,
  control_thumbnail_path TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  control_sha256 TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '[]',
  favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
  notes TEXT NOT NULL DEFAULT '',
  crop_settings TEXT NOT NULL DEFAULT '{}',
  strength REAL NOT NULL DEFAULT 0.8,
  preprocessor_revision TEXT NOT NULL,
  workflow_pack_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pose_assets_name ON pose_assets(name);
CREATE INDEX IF NOT EXISTS idx_pose_assets_scope ON pose_assets(scope, character_id);
