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
  scope_id: string | null;
  source_preset_id: string | null;
}
export interface RecipeRecord {
  id: string;
  name: string;
  character_id: string | null;
  freeform_prompt: string;
  negative_prompt: string;
  model_profile: 'photoreal_balanced' | 'preview_fast' | 'photoreal_max';
  preset_ids: string[];
  items: Array<{ preset_id: string; category: string; name: string; prompt: string }>;
  scope: 'global' | 'character' | 'project';
  scope_id: string | null;
  favorite: boolean;
  tags: string[];
  model_family: 'SDXL' | 'FLUX';
  model_file: string;
  lora_stack: Array<{ id: string; strength: number; clip_strength: number }>;
  identity_settings: Record<string, unknown>;
  pose_settings: Record<string, unknown>;
  variation_settings: Record<string, unknown>;
  video_settings: Record<string, unknown>;
  generation_settings: Record<string, unknown>;
}
export interface EngineComponent {
  id: string;
  display_name: string;
  state: string;
  progress: number;
  last_health_message: string;
  capabilities: string[];
  dependencies: string[];
  version: string;
  revision: string;
  source: string | null;
  sha256: string | null;
  license: { name: string; source_url?: string | null; acceptance_required?: boolean | null };
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
  elapsed_seconds?: number;
  result_generation_id?: string | null;
  created_at?: string;
  updated_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  operation?: 'generate' | 'inpaint' | 'upscale' | 'video';
  model_alias?: string;
  model_family?: string;
  output_width?: number | null;
  output_height?: number | null;
  progress_determinate?: boolean;
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
export interface VideoDurationProfile {
  id: 'safe' | 'standard' | 'extended';
  name: string;
  verified: boolean;
  enabled: boolean;
  duration_seconds: number;
  range_seconds?: [number, number];
  frame_count: number;
  expected_generation_seconds: number;
  estimated_vram_gb: number;
  estimated_ram_gb: number;
  estimated_disk_mb: number;
}
export interface VideoCapabilities {
  quality_profile: 'safe' | 'balanced' | 'quality';
  max_custom_seconds: number;
  extended_verified: boolean;
  historical_samples: number;
  profiles: VideoDurationProfile[];
}
export interface VideoSequenceSegment {
  id: string;
  position: number;
  source_generation_id: string;
  generation_id: string | null;
  job_id: string | null;
  motion_prompt: string;
  quality_profile: 'safe' | 'balanced' | 'quality';
  duration_profile: 'safe' | 'standard' | 'extended' | 'custom';
  duration_seconds: number;
  status: string;
  metadata: { error?: string | null };
}
export interface VideoSequence {
  id: string;
  name: string;
  source_generation_id: string;
  status: string;
  final_generation_id: string | null;
  segments: VideoSequenceSegment[];
}
export interface TrainingImage {
  id: string;
  dataset_id: string;
  original_name: string;
  width: number;
  height: number;
  blur_score: number;
  face_count: number | null;
  caption: string;
  warnings: Array<'low_resolution' | 'possible_blur' | 'near_duplicate' | 'multiple_faces'>;
}
export interface TrainingDataset {
  id: string;
  name: string;
  character_id: string | null;
  trigger_token: string;
  model_alias: 'photoreal_balanced' | 'preview_fast';
  notes: string;
  image_count: number;
  images: TrainingImage[];
  profiles: Record<
    'safe_12gb' | 'balanced_12gb',
    {
      display_name: string;
      resolution: number;
      rank: number;
      repeats: number;
      vram_gb: number;
      disk_gb: number;
    }
  >;
}
export interface TrainingCheckpoint {
  id: string;
  epoch: number;
  step: number;
  sha256: string;
  file_size: number;
  validation_sample_path: string | null;
  selected: boolean;
}
export interface TrainingRun {
  id: string;
  dataset_id: string;
  character_id: string | null;
  profile: 'safe_12gb' | 'balanced_12gb';
  status: string;
  progress: number;
  current_epoch: number;
  total_epochs: number;
  current_step: number;
  total_steps: number;
  eta_seconds: number | null;
  elapsed_seconds: number;
  error_message: string | null;
  failure: {
    category: string;
    title: string;
    explanation: string;
    recommended_recovery: string;
  } | null;
  resume_state_path: string | null;
  installed_lora_id: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  estimates: {
    profile: string;
    seconds: number;
    disk_gb: number;
    vram_gb: number;
    resolution: number;
    rank: number;
    steps: number;
  };
  checkpoints: TrainingCheckpoint[];
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
  system?: Record<string, string | number | null>;
  components?: Array<Record<string, unknown>>;
  model_packs?: Array<Record<string, unknown>>;
  runtime?: Record<string, unknown>;
}
