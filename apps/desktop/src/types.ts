export interface CharacterRecord {
  id: string;
  name: string;
  identity_description: string;
  archived: boolean;
  default_recipe_id: string | null;
  default_model_profile: string;
  reference_assets: string[];
  hair: string;
  eyes: string;
  facial_features: string;
  distinguishing_features: string;
  style_notes: string;
  body_notes: string;
  default_negative_prompt: string;
  references: CharacterReference[];
  loras: CharacterLora[];
}
export interface CharacterReference {
  id: string;
  thumbnail_path: string;
  crop_path: string;
  sha256: string;
  width: number;
  height: number;
  position: number;
  is_primary: boolean;
  notes: string;
}
export interface CharacterLora {
  id: string;
  name: string;
  model_family: string;
  trigger_token: string;
  position: number;
  strength: number;
  clip_strength: number;
  enabled: boolean;
}
export interface LoraRecord {
  id: string;
  name: string;
  filename: string;
  sha256: string;
  file_size: number;
  source_notes: string;
  license_notes: string;
  model_family: string;
  trigger_token: string;
  default_strength: number;
  default_clip_strength: number;
  enabled: boolean;
  verification_state: string;
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
    loras?: {
      id: string;
      name: string;
      filename: string;
      sha256: string;
      strength: number;
      clip_strength: number;
    }[];
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
