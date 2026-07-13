# Project Vanta Essential V1 Runbook

Last updated: 2026-07-13

## Repository audit

- Branch: `master`; working tree was clean at audit start.
- HEAD: `f2f2c8e WIP: preserve current Vanta work before Codex autopilot`.
- Pose checkpoint: `ca091af WIP: preserve pose control work before autopilot`.
- Audited: `AGENTS.md`, `README.md`, Git status/history, all five migrations, both engine manifests, the SDXL workflow descriptor, all current automated tests, all status/acceptance documents, the Python orchestrator, React renderer, shared domain/UI packages, Tauri lifecycle code, developer/build scripts, and local installed-data paths.
- Baseline automated evidence:
  - Python: 21 tests passed (`.venv\\Scripts\\python.exe -m pytest apps/orchestrator/tests -q -p no:cacheprovider`).
  - TypeScript domain and UI packages: strict type checks passed.
  - Desktop strict type check: failed at `apps/desktop/src/App.tsx:516` because the committed `PoseLibraryScreen` reference has no implementation.
- Installed-data evidence at `%APPDATA%\\studio.vanta.desktop`:
  - Preserved `flux_dev.safetensors`: 17,078,891,958 bytes.
  - Preserved `juggernautXL_version6Rundiffusion.safetensors`: 7,105,348,560 bytes.
  - Managed ComfyUI exists, but its `custom_nodes` directory has no installed pose preprocessor.
  - No managed Control LoRA and no persisted pose media are present.
- Existing production evidence preserved: authenticated loopback orchestration, hidden managed ComfyUI, SDXL generation, real Gallery media, persistent jobs/cancellation/retry, characters/references, SDXL LoRA application, variations, IP-Adapter identity conditioning, RealESRGAN/UltraSharp upscaling, Tauri/NSIS packaging, and Windows Job Object ownership.

## Completed features

- Preserved systems listed in the repository and `docs/CAPABILITY_EVIDENCE.md`; regression verification remains part of final acceptance.
- Pose Library is complete vertically:
  - Persisted queued extraction status, progress and failures survive restart; incomplete work is recovered safely.
  - Local/native file selection and drag/drop import, authenticated source/control previews, comparison view, search, character/global scope, tags, notes, favorite, default strength, edit, duplicate and delete are implemented.
  - Create can select a saved global or current-character pose, tune strength, open extraction, and use the 12 GB-safe 768x1024 profile. Gallery metadata and Generate Similar preserve pose provenance.
  - Character-scoped poses are rejected when used by a different character.
- Managed Pose Control is complete:
  - `comfyui_controlnet_aux` revision `e8b689a513c3e6b63edc44066560ca5919c0576e`, exact archive SHA-256 `20a064db4a175aabc66a4736e6c90aa13413279c465f62d819bd64de19a0c1fd`, plus pinned OpenCV 4.13.0.92 (SHA-256 `77a82fe35ddcec0f62c15f2ba8a12ecc2ed4207c17b0902c7a3151ae29f37fb6`) and exact DWPose ONNX assets.
  - DWPose model SHA-256 values are `724f4ff2439ed61afb86fb8a1951ec39c6220682803b4a8bd4f598cd913b1843` (`dw-ll_ucoco_384.onnx`) and `7860ae79de6c89a3c1eb72ae9a2756c0ccfbe04b7791bb5880afabd97855a411` (`yolox_l.onnx`).
  - The compatibility patch is deterministic (`dwpose-minimal-imports-003`), and install/verify/repair/remove use safe archive extraction, exact sizes/hashes, node registration checks and loopback-only ComfyUI health.
  - Xinsir OpenPose SDXL revision `229b885b1a6108259d8d0e128a726ba4416ce385`, 2,502,139,104 bytes, SHA-256 `b8524e557a7df60d081f5d4a0eb109967d107df217943bf88c2d99b9ebcc06c5`, is installed and verified under the stable `pose_xinsir_sdxl` alias.
- Managed Identity Lock is complete and composes with Pose:
  - `cubiq/ComfyUI_IPAdapter_plus` revision `a0f451a5113cf9becb0847b92884cb10cbdec0ef`, archive SHA-256 `c6c49c82aa65cb96b93bdf9f9b547f9c95310a2668a7a9aaa0285cccf4590347`, is installed with real node verification.
  - IP-Adapter Plus Face SDXL revision `018e402774aeeddd60609b4ecdb7e298259dc729`, 847,517,512 bytes, SHA-256 `677ad8860204f7d0bfba12d29e6c31ded9beefdf3e4bbd102518357d31a292c1`, and CLIP ViT-H, 2,528,373,448 bytes, SHA-256 `6ca9667da1ca9e0b0f75e46bb030f7e011f44f86cbfb8d5a36590fcd7507b030`, are installed and verified.
  - Identity-only, pose-only, variation and combined workflow versions are recorded distinctly; the combined graph applies IP-Adapter to the model before ControlNet conditioning.

## Current feature

Pose Control and managed Identity Lock are **complete pending the repository-wide pre-commit verification below**. The next independent Essential V1 slice is real inpainting and controlled variations.

Remaining V1 work:

- Inpainting/masking and controlled variations with real outputs.
- Separate FLUX adapter with native FLUX workflow/model selection and real output while preserving the installed 17.1 GB model.
- Local image-to-video, reference motion and video adapter.
- Local LoRA training, dataset checks and resumable run evidence.
- Preset/recipe mode completion and remaining Models & Engine diagnostics polish.
- Complete verification matrix, manual acceptance update and final NSIS installer evidence.

## Pose and Identity files changed

- `docs/ESSENTIAL_V1_RUNBOOK.md`
- Completed first vertical slice:
  - `apps/orchestrator/src/vanta_orchestrator/pose.py`
  - `apps/orchestrator/src/vanta_orchestrator/schemas.py`
  - `apps/orchestrator/src/vanta_orchestrator/app.py`
  - `apps/orchestrator/src/vanta_orchestrator/engine.py`
  - `apps/orchestrator/src/vanta_orchestrator/config.py`
  - `engine/manifests/core-components.v1.json`
  - `engine/manifests/model-packs.v1.json`
  - `apps/desktop/src/types.ts`
  - `apps/desktop/src/api.ts`
  - `apps/desktop/src/App.tsx`
  - `apps/desktop/src/styles.css`
  - `apps/orchestrator/tests/test_api.py`
  - `apps/orchestrator/tests/test_migrations.py`
  - `apps/orchestrator/tests/test_real_generation_domain.py`
  - `apps/desktop/src/App.test.tsx`

## Tests run

- Baseline Python suite: **21 passed**, 29 deprecation warnings.
- Baseline `packages/domain` strict TypeScript: **passed**.
- Baseline `packages/ui` strict TypeScript: **passed**.
- Baseline desktop strict TypeScript: **failed as expected** on missing `PoseLibraryScreen`.
- Workspace `pnpm` wrapper attempted to re-resolve dependencies from a different noninteractive store and was not allowed to modify the checkout; subsequent checks use existing project-local executables.
- Full Python suite after Pose/Identity completion: **23 passed**, 31 deprecation warnings.
- Pose/identity domain regression after provenance correction: **7 passed**.
- Desktop Vitest from its package configuration: **2 files / 2 tests passed**.
- Desktop, `packages/domain` and `packages/ui` strict TypeScript: **passed**.
- ESLint with zero warnings: **passed**.
- Ruff lint and format check: **passed** (16 Python files formatted).
- Prettier check on all changed web, manifest and runbook files: **passed**.
- UTF-8 JSON parse of both engine manifests: **passed**.
- Desktop production renderer build: **passed**, 1,656 modules, 281.02 kB JS / 38.48 kB CSS before gzip.
- Tauri `cargo check`: **passed**.

## Real evidence produced

- Real DWPose extraction from an existing user-owned Vanta generation:
  - Pose `pose-14dd2ca6615b4832aed84052fa39e1c7`.
  - Source SHA-256 `e8e99b60c92665cfa35c3fefe5d368f33cd62ae3504abc2b360f00b4b80dde1c`.
  - Control SHA-256 `4b367afc9c87f883f373b3aa4918ffa416fef6025c73925d178bdf42b8ddf5c8`.
  - Source/control comparison was visually inspected; the output contains a valid full-body, hand and face skeleton.
- Real pose-only generation:
  - Job `job-cb45f22fc2964575a5e7947bcfe67abd`, generation `generation-178f6fbb989d417b9b2cdcbe8b6907af`.
  - 768x1024, 25 steps, 27.94 seconds; visually inspected as structurally faithful and artifact-free at acceptance scale.
- Real Identity Lock + Pose generation after provenance correction:
  - Job `job-968e4598d67c4986ace50fff2676d7ef`, generation `generation-113962b2ce644d2b93e27192fd83eb2a`.
  - Identity reference `reference-471eea7826ea43808d31ad8ce5292d49`, SHA-256 `4a4e92b6db7a0f33828006bfa2fe7e1a8ed74155701b8cce6e077286cc8c9f38`.
  - 768x1024, 25 steps, 51.75 seconds, workflow `image-sdxl-identity-pose-v1`; the same pose hashes and strength 0.75 are stored in Gallery metadata.
  - Visually inspected against the source: recognizable fictional-character identity, intended upright full-body pose, coherent anatomy and no text/watermark.
- All evidence is persisted under `%APPDATA%\\studio.vanta.desktop`; no fixture or fake Ready state was used. Existing large models remain present and untouched.

## Blockers

- None currently.
- Upstream pose and identity packages/models require user license review and acceptance. Manifests preserve exact source, revision, size, hash and license metadata; the UI does not claim redistribution review has occurred.

## Exact next action

Commit the verified Pose + Identity slice, then audit and finish real inpainting and controlled variations vertically.

## Final acceptance status

**Not accepted. Essential V1 remains in progress.**
