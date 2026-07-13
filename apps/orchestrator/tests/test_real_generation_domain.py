from __future__ import annotations

import json
from pathlib import Path

import pytest

from vanta_orchestrator.comfy_runtime import ensure_safe_archive_members, validate_safetensors
from vanta_orchestrator.engine import WorkflowCompiler


def test_prompt_compilation_is_stable_and_workflow_is_local_comfy_api_shape():
    request = {
        "character_identity": "original adult character",
        "wardrobe": "black silk dress",
        "expression": "calm expression",
        "pose": "standing",
        "location": "editorial studio",
        "lighting": "soft key light",
        "camera": "85mm portrait",
        "quality": "photorealistic",
        "direction": "restrained fashion editorial",
        "custom_tags": ["cinematic", "35mm"],
        "negative_prompt": "low quality",
        "seed": 42,
        "width": 832,
        "height": 1216,
        "steps": 30,
        "guidance": 5.5,
    }
    prompt = WorkflowCompiler.compile_prompt(request)
    assert prompt.startswith("original adult character, black silk dress, calm expression")
    workflow = WorkflowCompiler().compile(request, "model.safetensors")
    assert [workflow[str(node)]["class_type"] for node in range(1, 8)] == [
        "CheckpointLoaderSimple",
        "CLIPTextEncode",
        "CLIPTextEncode",
        "EmptyLatentImage",
        "KSampler",
        "VAEDecode",
        "SaveImage",
    ]
    assert workflow["4"]["inputs"]["width"] == 832
    assert workflow["4"]["inputs"]["batch_size"] == 1


def test_safetensors_validation_and_archive_path_safety(tmp_path: Path):
    checkpoint = tmp_path / "model.safetensors"
    header = json.dumps({"__metadata__": {"format": "pt"}}).encode("utf-8")
    checkpoint.write_bytes(len(header).to_bytes(8, "little") + header + b"payload")
    assert validate_safetensors(checkpoint)["__metadata__"]["format"] == "pt"
    with pytest.raises(RuntimeError, match="unsafe"):
        ensure_safe_archive_members(["ComfyUI/../escape.txt"])


def test_sdxl_lora_workflow_is_inserted_without_exposing_nodes_to_the_ui():
    request = {
        "direction": "original adult editorial portrait",
        "negative_prompt": "",
        "seed": 7,
        "width": 832,
        "height": 1216,
        "steps": 2,
        "guidance": 5.5,
    }
    workflow = WorkflowCompiler().compile(
        request,
        "model.safetensors",
        [{"filename": "local-style.safetensors", "strength": 0.7, "clip_strength": 0.8}],
    )
    assert workflow["8"]["class_type"] == "LoraLoader"
    assert workflow["5"]["inputs"]["model"] == ["8", 0]
    assert workflow["2"]["inputs"]["clip"] == ["8", 1]


def test_variation_workflow_encodes_a_local_source_image():
    request = {
        "direction": "original adult editorial portrait variation",
        "negative_prompt": "",
        "seed": 8,
        "width": 832,
        "height": 1216,
        "steps": 2,
        "guidance": 5.5,
        "variation_strength": 0.42,
    }
    workflow = WorkflowCompiler().compile(
        request, "model.safetensors", source_image_name="Vanta/source.png"
    )
    assert workflow["9"]["class_type"] == "LoadImage"
    assert workflow["10"]["class_type"] == "VAEEncode"
    assert workflow["5"]["inputs"]["latent_image"] == ["10", 0]
    assert workflow["5"]["inputs"]["denoise"] == 0.42


def test_upscale_workflow_uses_the_local_model_loader_and_native_tiled_execution_node():
    workflow = WorkflowCompiler.upscale("Vanta/source.png", "RealESRGAN_x2plus.pth")
    assert workflow["1"]["class_type"] == "LoadImage"
    assert workflow["2"]["class_type"] == "UpscaleModelLoader"
    assert workflow["2"]["inputs"]["model_name"] == "RealESRGAN_x2plus.pth"
    assert workflow["3"]["class_type"] == "ImageUpscaleWithModel"
    assert workflow["4"]["class_type"] == "SaveImage"
