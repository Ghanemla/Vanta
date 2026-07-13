PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS training_datasets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  character_id TEXT REFERENCES characters(id) ON DELETE SET NULL,
  trigger_token TEXT NOT NULL,
  model_alias TEXT NOT NULL DEFAULT 'photoreal_balanced',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS training_images (
  id TEXT PRIMARY KEY,
  dataset_id TEXT NOT NULL REFERENCES training_datasets(id) ON DELETE CASCADE,
  image_path TEXT NOT NULL,
  thumbnail_path TEXT NOT NULL,
  original_name TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  perceptual_hash TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  blur_score REAL NOT NULL,
  face_count INTEGER,
  caption TEXT NOT NULL DEFAULT '',
  warnings TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(dataset_id, sha256)
);
CREATE INDEX IF NOT EXISTS idx_training_images_dataset ON training_images(dataset_id, created_at);

CREATE TABLE IF NOT EXISTS training_runs (
  id TEXT PRIMARY KEY,
  dataset_id TEXT NOT NULL REFERENCES training_datasets(id) ON DELETE RESTRICT,
  character_id TEXT REFERENCES characters(id) ON DELETE SET NULL,
  profile TEXT NOT NULL,
  status TEXT NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  current_epoch INTEGER NOT NULL DEFAULT 0,
  total_epochs INTEGER NOT NULL,
  current_step INTEGER NOT NULL DEFAULT 0,
  total_steps INTEGER NOT NULL,
  eta_seconds INTEGER,
  model_alias TEXT NOT NULL,
  output_name TEXT NOT NULL,
  output_dir TEXT NOT NULL,
  parameters TEXT NOT NULL,
  estimates TEXT NOT NULL,
  resume_state_path TEXT,
  selected_checkpoint_id TEXT,
  installed_lora_id TEXT REFERENCES lora_packs(id) ON DELETE SET NULL,
  error_message TEXT,
  cancellation_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancellation_requested IN (0, 1)),
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_training_runs_created ON training_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS training_checkpoints (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES training_runs(id) ON DELETE CASCADE,
  epoch INTEGER NOT NULL,
  step INTEGER NOT NULL,
  file_path TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  file_size INTEGER NOT NULL,
  validation_sample_path TEXT,
  selected INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0, 1)),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_training_checkpoints_run ON training_checkpoints(run_id, epoch, step);
