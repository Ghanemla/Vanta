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
export interface PoseRecord {
  id: string;
  name: string;
  scope: 'global' | 'character';
  character_id: string | null;
  source_thumbnail_path: string;
  control_thumbnail_path: string;
  tags: string[];
  favorite: boolean;
  notes: string;
  strength: number;
  source_sha256: string;
  control_sha256: string;
  preprocessor_revision: string;
  workflow_pack_version: string;
  status: 'queued' | 'starting' | 'extracting' | 'saving' | 'ready' | 'failed';
  progress: number;
  error_message: string | null;
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
  character_id?: string | null;
  recipe_id?: string | null;
  image_path: string;
  media_type: 'image' | 'video';
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
    source_generation_id?: string | null;
    derivative_of?: string | null;
    variation_strength?: number;
    variation_mode?: string;
    variation_prompt?: string;
    operation?: string;
    inpaint?: {
      mask_path: string;
      mask_sha256: string;
      region_prompt: string;
      region_negative_prompt: string;
      denoise_strength: number;
      outside_mask_composite: boolean;
    } | null;
    fps?: number;
    frame_count?: number;
    duration_seconds?: number;
    motion_prompt?: string;
    motion_reference?: {
      id: string;
      name: string;
      trim: [number, number];
      fit_mode: 'crop' | 'fit';
      smoothing: number;
      strength: number;
      broad_motion_prompt: string;
      identity_transfer: false;
      audio_transfer: false;
      branding_transfer: false;
    } | null;
    pose_control?: {
      id: string;
      name: string;
      scope: 'global' | 'character';
      strength: number;
      source_sha256: string;
      control_sha256: string;
    } | null;
    request?: Record<string, unknown>;
  };
  created_at: string;
}
export interface GenerationJob {
  id: string;
  status: string;
  progress: number;
  error_message?: string | null;
  current_step?: number | null;
  total_steps?: number | null;
  queue_position?: number | null;
  eta_seconds?: number | null;
  result_generation_id?: string | null;
  created_at?: string;
}
export interface MotionAsset {
  id: string;
  name: string;
  source_path: string;
  preview_path: string | null;
  thumbnail_path: string | null;
  start_seconds: number;
  end_seconds: number;
  fit_mode: 'crop' | 'fit';
  smoothing: number;
  strength: number;
  status: 'queued' | 'extracting' | 'encoding' | 'ready' | 'failed';
  progress: number;
  error_message: string | null;
  metadata: {
    source_duration_seconds: number;
    rights_confirmed: boolean;
    broad_motion_prompt?: string;
    extracted_frames?: number;
    sample_fps?: number;
    face_extraction?: false;
    audio_transfer?: false;
    source_branding_transfer?: false;
    transfer_policy: string;
  };
  created_at: string;
  updated_at: string;
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
