ALTER TABLE presets ADD COLUMN scope_id TEXT;

ALTER TABLE recipes ADD COLUMN scope TEXT NOT NULL DEFAULT 'global';
ALTER TABLE recipes ADD COLUMN scope_id TEXT;
ALTER TABLE recipes ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1));
ALTER TABLE recipes ADD COLUMN tags TEXT NOT NULL DEFAULT '[]';
ALTER TABLE recipes ADD COLUMN model_family TEXT NOT NULL DEFAULT 'SDXL';
ALTER TABLE recipes ADD COLUMN model_file TEXT NOT NULL DEFAULT '';
ALTER TABLE recipes ADD COLUMN lora_stack TEXT NOT NULL DEFAULT '[]';
ALTER TABLE recipes ADD COLUMN identity_settings TEXT NOT NULL DEFAULT '{}';
ALTER TABLE recipes ADD COLUMN pose_settings TEXT NOT NULL DEFAULT '{}';
ALTER TABLE recipes ADD COLUMN variation_settings TEXT NOT NULL DEFAULT '{}';
ALTER TABLE recipes ADD COLUMN video_settings TEXT NOT NULL DEFAULT '{}';
ALTER TABLE recipes ADD COLUMN generation_settings TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_presets_scope ON presets(scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_recipes_scope ON recipes(scope, scope_id, updated_at DESC);
