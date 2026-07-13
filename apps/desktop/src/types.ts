export interface CharacterRecord {
  id: string;
  name: string;
  identity_description: string;
  archived: boolean;
  default_recipe_id: string | null;
  default_model_profile: string;
  reference_assets: string[];
}
export interface PresetRecord {
  id: string;
  category: string;
  name: string;
  prompt: string;
  negative_prompt: string;
  tags: string[];
  favorite: boolean;
  origin: 'builtin' | 'user';
  scope: string;
  source_preset_id: string | null;
}
export interface EngineComponent {
  id: string;
  display_name: string;
  state: string;
  progress: number;
  last_health_message: string;
  capabilities: string[];
  dependencies: string[];
}
export interface ModelPack {
  id: string;
  alias: string;
  display_name: string;
  state: string;
  installed: boolean;
  verified: boolean;
  is_default: boolean;
  progress: number;
  model_family: string;
  capabilities: string[];
  disk_gb: number;
  hardware: { minimum_vram_gb: number; recommended_vram_gb: number; minimum_ram_gb: number };
  download: { source_name: string; authentication: string };
  license: { name: string; acceptance_required: boolean };
  sha256: string;
  target_path: string;
  compatible_workflows: string[];
  recommended: boolean;
  installed_path?: string | null;
  original_path?: string | null;
  file_size?: number | null;
  imported_at?: string | null;
  filename?: string;
  source_information?: string;
}
export interface GenerationRecord {
  id: string;
  image_path: string;
  prompt: string;
  negative_prompt: string;
  seed: number;
  model_alias: string;
  width: number;
  height: number;
  thumbnail_path?: string | null;
  metadata: {
    recipe?: string;
    steps: number;
    guidance: number;
    disclosure: boolean;
    compiled_positive_prompt?: string;
    model_filename?: string;
    model_sha256?: string;
    workflow_version?: string;
    request?: Record<string, unknown>;
  };
  created_at: string;
}
export interface GenerationJob {
  id: string;
  status: string;
  progress: number;
  error_message?: string | null;
}
export interface SettingsRecord {
  values: Record<string, string>;
  paths: { data: string; database: string; models: string };
}
export interface Diagnostics {
  summary: string;
  messages: string[];
  raw_logs: string[];
  service?: Record<string, string | number | null>;
}
