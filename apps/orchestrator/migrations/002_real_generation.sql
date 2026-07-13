ALTER TABLE model_packs ADD COLUMN installed_path TEXT;
ALTER TABLE model_packs ADD COLUMN original_path TEXT;
ALTER TABLE model_packs ADD COLUMN file_size INTEGER;
ALTER TABLE model_packs ADD COLUMN license_notes TEXT NOT NULL DEFAULT '';
ALTER TABLE model_packs ADD COLUMN imported_at TEXT;

ALTER TABLE generation_jobs ADD COLUMN progress INTEGER NOT NULL DEFAULT 0;
ALTER TABLE generation_jobs ADD COLUMN prompt_id TEXT;
ALTER TABLE generation_jobs ADD COLUMN started_at TEXT;
ALTER TABLE generation_jobs ADD COLUMN completed_at TEXT;

ALTER TABLE generations ADD COLUMN thumbnail_path TEXT;

DELETE FROM generations WHERE image_path LIKE 'fixture://%';
