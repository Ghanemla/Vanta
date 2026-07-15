from __future__ import annotations

import json
from pathlib import Path

import pytest

from vanta_orchestrator.comfy_runtime import (
    REQUIRED_RUNTIME_NODES,
    ManagedComfyRuntime,
    ensure_safe_archive_members,
    validate_safetensors,
)
from vanta_orchestrator.config import Settings
from vanta_orchestrator.engine import (
    POSE_CONTROL_FILENAME,
    FluxWorkflowCompiler,
    WorkflowCompiler,
    checkpoint_family,
)
from vanta_orchestrator.pose import PoseService
from vanta_orchestrator.video import LtxVideoWorkflowCompiler, MotionService, encode_mp4


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


def test_runtime_ready_requires_inventory_and_the_live_required_node_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    settings = Settings(data_dir=tmp_path / "data")
    source = {"sha256": "a" * 64, "bytes": 10, "url": "https://example.invalid/runtime.7z"}
    runtime = ManagedComfyRuntime(settings, source, "v-test")
    main = runtime.root / "ComfyUI" / "main.py"
    python = runtime.root / "python_embeded" / "python.exe"
    main.parent.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    main.write_text("# managed runtime", encoding="utf-8")
    python.write_bytes(b"MZ")
    (runtime.root / ".vanta-runtime.json").write_text(
        json.dumps(
            {
                "revision": "v-test",
                "archive_sha256": "a" * 64,
                "packaged_python_probe": "torch-test",
                "extracted_bytes": 100,
            }
        ),
        encoding="utf-8",
    )
    assert runtime.installed_layout() == (main, python)

    monkeypatch.setattr(
        runtime,
        "_request_json",
        lambda path, payload=None: (
            {} if path == "/system_stats" else {node: {} for node in REQUIRED_RUNTIME_NODES}
        ),
    )
    assert runtime.wait_healthy(timeout=1)
    assert runtime.snapshot().state == "ready"

    monkeypatch.setattr(
        runtime,
        "_request_json",
        lambda path, payload=None: {} if path == "/system_stats" else {},
    )
    assert not runtime.wait_healthy(timeout=1)
    assert runtime.snapshot().state == "repair_needed"


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


def test_flux_workflow_is_a_distinct_native_adapter_with_safe_defaults_and_loras():
    request = {
        "direction": "original adult editorial portrait",
        "negative_prompt": "text, watermark",
        "seed": 17,
        "width": 768,
        "height": 1024,
        "steps": 20,
        "guidance": 3.5,
    }
    workflow = FluxWorkflowCompiler().compile(
        request,
        "flux_dev.safetensors",
        [{"filename": "flux-style.safetensors", "strength": 0.7, "clip_strength": 0.8}],
    )
    assert workflow["1"]["class_type"] == "CheckpointLoaderSimple"
    assert workflow["3"]["class_type"] == "FluxGuidance"
    assert workflow["5"]["class_type"] == "EmptySD3LatentImage"
    assert workflow["6"]["inputs"]["cfg"] == 1.0
    assert workflow["6"]["inputs"]["scheduler"] == "simple"
    assert workflow["20"]["class_type"] == "LoraLoader"
    assert workflow["6"]["inputs"]["model"] == ["20", 0]
    assert workflow["2"]["inputs"]["clip"] == ["20", 1]


def test_checkpoint_family_requires_self_contained_flux_assets():
    header = {
        "model.diffusion_model.double_blocks.0.img_attn.proj.weight": {},
        "text_encoders.t5xxl.transformer.encoder.block.0.weight": {},
        "vae.decoder.conv_in.weight": {},
    }
    assert checkpoint_family(header) == "FLUX"
    assert (
        checkpoint_family(
            {
                "model.diffusion_model.input_blocks.0.0.weight": {},
                "conditioner.embedders.0.transformer.text_model.embeddings.weight": {},
                "conditioner.embedders.1.model.text_projection": {},
                "first_stage_model.decoder.conv_out.weight": {},
            }
        )
        == "SDXL"
    )
    assert checkpoint_family({"model.diffusion_model.input_blocks.0.0.weight": {}}) == "UNKNOWN"


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


def test_inpaint_workflow_masks_sampling_and_composites_over_the_untouched_source():
    request = {
        "region_prompt": "tailored rose blazer with natural fabric folds",
        "region_negative_prompt": "text, malformed fabric",
        "seed": 11,
        "steps": 24,
        "guidance": 5.0,
        "inpaint_strength": 0.62,
    }
    workflow = WorkflowCompiler().compile_inpaint(
        request,
        "model.safetensors",
        "Vanta/source.png",
        "Vanta/mask.png",
    )
    assert workflow["20"]["class_type"] == "LoadImage"
    assert workflow["21"]["class_type"] == "LoadImageMask"
    assert workflow["21"]["inputs"]["channel"] == "red"
    assert workflow["4"]["class_type"] == "VAEEncodeForInpaint"
    assert workflow["5"]["inputs"]["denoise"] == 0.62
    assert workflow["22"]["class_type"] == "ImageCompositeMasked"
    assert workflow["22"]["inputs"]["destination"] == ["20", 0]
    assert workflow["22"]["inputs"]["mask"] == ["21", 0]
    assert workflow["7"]["inputs"]["images"] == ["22", 0]


def test_upscale_workflow_uses_the_local_model_loader_and_native_tiled_execution_node():
    workflow = WorkflowCompiler.upscale("Vanta/source.png", "RealESRGAN_x2plus.pth")
    assert workflow["1"]["class_type"] == "LoadImage"
    assert workflow["2"]["class_type"] == "UpscaleModelLoader"
    assert workflow["2"]["inputs"]["model_name"] == "RealESRGAN_x2plus.pth"
    assert workflow["3"]["class_type"] == "ImageUpscaleWithModel"
    assert workflow["4"]["class_type"] == "SaveImage"


def test_identity_workflow_uses_the_managed_ipadapter_not_a_prompt_only_substitute():
    request = {
        "direction": "original adult editorial portrait",
        "negative_prompt": "",
        "seed": 9,
        "width": 832,
        "height": 1216,
        "steps": 2,
        "guidance": 5.5,
    }
    workflow = WorkflowCompiler().compile(
        request, "model.safetensors", identity_image_name="Vanta/identity-reference.png"
    )
    assert workflow["21"]["class_type"] == "IPAdapterUnifiedLoader"
    assert workflow["21"]["inputs"]["preset"] == "PLUS FACE (portraits)"
    assert workflow["22"]["class_type"] == "IPAdapterAdvanced"
    assert workflow["5"]["inputs"]["model"] == ["22", 0]


def test_pose_extraction_and_identity_pose_generation_use_managed_nodes():
    extraction = PoseService._extraction_workflow("pose-1", "owned-reference.png")
    assert extraction["2"]["class_type"] == "DWPreprocessor"
    assert extraction["2"]["inputs"]["bbox_detector"] == "yolox_l.onnx"
    assert extraction["2"]["inputs"]["pose_estimator"] == "dw-ll_ucoco_384.onnx"

    request = {
        "direction": "original adult editorial portrait",
        "negative_prompt": "",
        "seed": 10,
        "width": 768,
        "height": 1024,
        "steps": 2,
        "guidance": 5.5,
    }
    workflow = WorkflowCompiler().compile(
        request,
        "model.safetensors",
        identity_image_name="Vanta/identity-reference.png",
        pose_image_name="Vanta/pose.png",
        pose_strength=0.7,
    )
    assert workflow["31"]["class_type"] == "DiffControlNetLoader"
    assert workflow["31"]["inputs"]["control_net_name"] == POSE_CONTROL_FILENAME
    assert workflow["32"]["inputs"]["strength"] == 0.7
    assert workflow["31"]["inputs"]["model"] == ["22", 0]
    assert (
        WorkflowCompiler.workflow_version(identity_image=True, pose_image=True)
        == "image-sdxl-identity-pose-v1"
    )
    assert WorkflowCompiler.workflow_version(source_image=True) == "image-sdxl-variation-img2img-v1"


def test_ltx_video_workflow_uses_native_distilled_sampling_and_exact_frame_count():
    workflow = LtxVideoWorkflowCompiler().compile(
        {
            "motion_prompt": "subtle breathing and a gentle camera drift",
            "negative_prompt": "text, identity change",
            "profile": "safe",
            "duration_seconds": 2,
            "seed": 44,
            "motion_strength": 0.65,
        },
        "Vanta/source.png",
    )
    assert workflow["7"]["class_type"] == "LTXVImgToVideo"
    assert workflow["7"]["inputs"]["length"] == 49
    assert workflow["10"]["inputs"]["sampler_name"] == "euler_ancestral"
    assert workflow["11"]["inputs"]["sigmas"].endswith(", 0.0")
    assert workflow["12"]["class_type"] == "SamplerCustomAdvanced"
    assert workflow["15"]["class_type"] == "SaveImage"


def test_managed_mp4_encoder_creates_a_playable_local_video(tmp_path: Path):
    import imageio_ffmpeg
    from PIL import Image

    frames = []
    for index in range(4):
        path = tmp_path / f"frame-{index}.png"
        Image.new("RGB", (64, 64), (40 + index * 30, 12, 50)).save(path)
        frames.append(path)
    output = tmp_path / "preview.mp4"
    encode_mp4(frames, output, 8)
    count, duration = imageio_ffmpeg.count_frames_and_secs(str(output))
    assert output.is_file() and output.stat().st_size > 1000
    assert count == 4
    assert duration == pytest.approx(0.5, abs=0.1)


def test_reference_motion_description_is_identity_safe(tmp_path: Path):
    from PIL import Image, ImageDraw

    paths = []
    for index, x in enumerate((100, 145, 190)):
        path = tmp_path / f"pose-{index}.png"
        image = Image.new("RGB", (512, 512), "black")
        ImageDraw.Draw(image).rectangle((x, 120, x + 100, 420), fill="white")
        image.save(path)
        paths.append(path)
    prompt = MotionService._describe_motion(paths)
    assert "right" in prompt
    assert "exclude any reference-person identity" in prompt
    assert "face" not in prompt.lower()
