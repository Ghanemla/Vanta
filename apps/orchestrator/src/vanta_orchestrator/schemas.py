from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CharacterInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    identity_description: str = Field(default="", max_length=8000)
    default_recipe_id: str | None = None
    default_model_profile: str = "photoreal_balanced"
    hair: str = Field(default="", max_length=1000)
    eyes: str = Field(default="", max_length=1000)
    facial_features: str = Field(default="", max_length=2000)
    distinguishing_features: str = Field(default="", max_length=2000)
    style_notes: str = Field(default="", max_length=2000)
    body_notes: str = Field(default="", max_length=2000)
    default_negative_prompt: str = Field(default="", max_length=8000)
    reference_assets: list[str] = []


class ReferenceImportInput(StrictModel):
    source_path: str = Field(min_length=1, max_length=32767)
    notes: str = Field(default="", max_length=2000)


class ReferenceUpdateInput(StrictModel):
    notes: str = Field(default="", max_length=2000)
    position: int = Field(default=0, ge=0)
    is_primary: bool = False


class LoraImportInput(StrictModel):
    source_path: str = Field(min_length=1, max_length=32767)
    name: str = Field(min_length=1, max_length=120)
    source_notes: str = Field(default="", max_length=2000)
    license_notes: str = Field(default="", max_length=2000)
    trigger_token: str = Field(default="", max_length=500)
    default_strength: float = Field(default=1.0, ge=0, le=2)
    default_clip_strength: float = Field(default=1.0, ge=0, le=2)


class CharacterLoraInput(StrictModel):
    lora_id: str = Field(min_length=1)
    position: int = Field(default=0, ge=0)
    strength: float = Field(default=1.0, ge=0, le=2)
    clip_strength: float = Field(default=1.0, ge=0, le=2)
    enabled: bool = True


class PresetInput(StrictModel):
    category: str
    name: str = Field(min_length=1, max_length=100)
    prompt: str = Field(max_length=8000)
    negative_prompt: str = Field(default="", max_length=8000)
    tags: list[str] = []
    favorite: bool = False
    scope: Literal["global", "character", "project"] = "global"


class RecipeInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    character_id: str | None = None
    freeform_prompt: str = ""
    negative_prompt: str = ""
    model_profile: str = "photoreal_balanced"
    preset_ids: list[str] = []


class SettingInput(StrictModel):
    value: str


class ModelImportInput(StrictModel):
    source_path: str = Field(min_length=1, max_length=32767)
    alias: Literal["photoreal_balanced"] = "photoreal_balanced"
    license_notes: str = Field(default="", max_length=2000)


class UpscalerImportInput(StrictModel):
    source_path: str = Field(min_length=1, max_length=32767)
    alias: Literal["realesrgan_x2plus", "ultrasharp_x4"]
    license_notes: str = Field(default="", max_length=2000)


class GenerationInput(StrictModel):
    operation: Literal["generate", "upscale"] = "generate"
    character_id: str | None = None
    recipe_id: str | None = None
    character_identity: str = Field(default="", max_length=8000)
    wardrobe: str = Field(default="", max_length=8000)
    expression: str = Field(default="", max_length=8000)
    pose: str = Field(default="", max_length=8000)
    location: str = Field(default="", max_length=8000)
    lighting: str = Field(default="", max_length=8000)
    camera: str = Field(default="", max_length=8000)
    quality: str = Field(default="", max_length=8000)
    direction: str = Field(default="", max_length=8000)
    custom_tags: list[str] = Field(default_factory=list, max_length=20)
    negative_prompt: str = Field(default="", max_length=8000)
    model_alias: Literal["photoreal_balanced", "preview_fast", "photoreal_max"] = (
        "photoreal_balanced"
    )
    seed: int = Field(ge=0, le=2**63 - 1)
    width: int = Field(default=832, ge=512, le=1536, multiple_of=64)
    height: int = Field(default=1216, ge=512, le=1536, multiple_of=64)
    steps: int = Field(default=30, ge=1, le=60)
    guidance: float = Field(default=5.5, ge=1, le=15)
    lora_ids: list[str] = Field(default_factory=list, max_length=8)
    source_generation_id: str | None = None
    variation_strength: float = Field(default=0.45, ge=0.05, le=0.95)
    upscale_profile: Literal["realesrgan_x2plus", "ultrasharp_x4"] | None = None
