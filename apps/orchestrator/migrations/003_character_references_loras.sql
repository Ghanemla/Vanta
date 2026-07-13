ALTER TABLE characters ADD COLUMN avatar_path TEXT;
ALTER TABLE characters ADD COLUMN hair TEXT NOT NULL DEFAULT '';
ALTER TABLE characters ADD COLUMN eyes TEXT NOT NULL DEFAULT '';
ALTER TABLE characters ADD COLUMN facial_features TEXT NOT NULL DEFAULT '';
ALTER TABLE characters ADD COLUMN distinguishing_features TEXT NOT NULL DEFAULT '';
ALTER TABLE characters ADD COLUMN style_notes TEXT NOT NULL DEFAULT '';
ALTER TABLE characters ADD COLUMN body_notes TEXT NOT NULL DEFAULT '';
ALTER TABLE characters ADD COLUMN default_negative_prompt TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS character_references (
  id TEXT PRIMARY KEY,
  character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  image_path TEXT NOT NULL,
  thumbnail_path TEXT NOT NULL,
  crop_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  width INTEGER NOT NULL,
  height INTEGER NOT NULL,
  position INTEGER NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(character_id, sha256)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_character_references_primary
  ON character_references(character_id) WHERE is_primary = 1;
CREATE INDEX IF NOT EXISTS idx_character_references_character ON character_references(character_id, position);

CREATE TABLE IF NOT EXISTS lora_packs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  filename TEXT NOT NULL,
  installed_path TEXT NOT NULL UNIQUE,
  original_path TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  file_size INTEGER NOT NULL,
  source_notes TEXT NOT NULL DEFAULT '',
  license_notes TEXT NOT NULL DEFAULT '',
  model_family TEXT NOT NULL,
  trigger_token TEXT NOT NULL DEFAULT '',
  default_strength REAL NOT NULL DEFAULT 1.0,
  default_clip_strength REAL NOT NULL DEFAULT 1.0,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  verification_state TEXT NOT NULL DEFAULT 'ready',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS character_loras (
  character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  lora_id TEXT NOT NULL REFERENCES lora_packs(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  strength REAL NOT NULL DEFAULT 1.0,
  clip_strength REAL NOT NULL DEFAULT 1.0,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  PRIMARY KEY (character_id, lora_id)
);
CREATE INDEX IF NOT EXISTS idx_character_loras_order ON character_loras(character_id, position);
