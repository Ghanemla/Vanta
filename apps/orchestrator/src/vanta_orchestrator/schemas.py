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
    reference_assets: list[str] = []


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


class GenerationInput(StrictModel):
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
