# Engine Packs and Installation Policy

## User-facing rule

Normal users interact with capabilities, not ComfyUI nodes.

Examples:

- Pose Control
- Identity Lock
- Face Detail
- Inpainting
- Upscaling
- Motion Reference
- LoRA Training

Each capability reports one of:

- Ready
- Installing
- Update available
- Repair needed
- Unsupported on this hardware

## Required components

Vanta owns a versioned core engine manifest. Core dependencies are installed, pinned, checked and repaired by the application.

Initial shipped core:

- Managed local ComfyUI runtime pinned to v0.27.0
- Reviewed 7-Zip console extractor, bundled with its source, version, license and SHA-256 in the Vanta manifest so archive extraction never depends on a system 7-Zip installation
- Vanta workflow/compiler package
- pose preprocessing and ControlNet support
- identity adapter support
- image upload/output utilities
- generation metadata support
- one compatible upscaler
- required Python packages
- pinned local SDXL LoRA trainer and offline tokenizer assets
- pinned local ONNX captioner with identity-name tag exclusion

Users should never be asked to locate a missing node manually during normal operation.

Training datasets, captions, checkpoints and resumable run state are user data and survive component repair or removal. Only the managed trainer/captioner runtime is removed by a component Remove action.

## Model packs

Large model files are not treated like application code. They are selectable model packs with:

- display name and capability tags
- model family and compatibility
- expected disk and VRAM use
- source and license metadata
- hashes
- install path
- installed/verified state
- optional authentication or license-acceptance step
- recommended workflows
- active/default status

Actions:

- Install
- Pause/resume
- Verify
- Repair
- Remove
- Switch default

## Initial profiles

### Realistic — Balanced (default development profile)

Use an SDXL-compatible photoreal workflow as the dependable 12 GB path. The implementation must reference a logical model alias rather than a hard-coded community checkpoint filename. First-run setup resolves that alias to an installed compatible model.

### Realistic — Maximum

Optional quantized FLUX-compatible profile. It may require extra components and stricter license handling, so it must not be the sole required engine or silently bundled.

### Preview — Fast

Lower-step SDXL-compatible preview profile for rapid composition tests.

## First-run experience

1. Detect GPU, VRAM, RAM, disk space and installed drivers.
2. Install/verify the core engine.
3. Show one recommended local model pack for the detected hardware.
4. Explain download size and license before downloading.
5. Download with progress, pause/resume and hash verification.
6. Run a small diagnostic generation.
7. Mark the studio Ready or show a human-readable repair action.

## Failure policy

Never display raw “missing node” errors as the primary user experience.

Translate engine failures into actions:

- “Pose Control needs repair”
- “The selected model is not installed”
- “Not enough VRAM for Maximum Quality; use Balanced”
- “Model file failed verification”
- “Restart the local engine”

Keep full logs in a diagnostics view and exportable support bundle.

## Commercial-readiness rule

Do not redistribute a model or custom component without recording and reviewing its license. Keep the engine adapter capable of supporting user-installed models that cannot legally be bundled.
