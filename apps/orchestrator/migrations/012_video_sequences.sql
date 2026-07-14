CREATE TABLE video_sequences (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_generation_id TEXT NOT NULL REFERENCES generations(id),
  character_id TEXT REFERENCES characters(id) ON DELETE SET NULL,
  model_alias TEXT NOT NULL DEFAULT 'video_ltx_2b',
  status TEXT NOT NULL DEFAULT 'draft',
  final_generation_id TEXT REFERENCES generations(id) ON DELETE SET NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE video_sequence_segments (
  id TEXT PRIMARY KEY,
  sequence_id TEXT NOT NULL REFERENCES video_sequences(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  source_generation_id TEXT NOT NULL REFERENCES generations(id),
  generation_id TEXT REFERENCES generations(id) ON DELETE SET NULL,
  job_id TEXT REFERENCES generation_jobs(id) ON DELETE SET NULL,
  motion_prompt TEXT NOT NULL,
  negative_prompt TEXT NOT NULL DEFAULT '',
  quality_profile TEXT NOT NULL DEFAULT 'safe',
  duration_profile TEXT NOT NULL DEFAULT 'safe',
  duration_seconds INTEGER NOT NULL DEFAULT 2,
  seed INTEGER NOT NULL,
  motion_asset_id TEXT REFERENCES motion_assets(id) ON DELETE SET NULL,
  motion_strength REAL NOT NULL DEFAULT 0.65,
  status TEXT NOT NULL DEFAULT 'queued',
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(sequence_id, position)
);

CREATE INDEX idx_video_sequences_created ON video_sequences(created_at DESC);
CREATE INDEX idx_video_sequence_segments_sequence ON video_sequence_segments(sequence_id, position);
