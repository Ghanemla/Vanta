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

Local image-to-video and identity-safe Reference Motion are **implemented with current-code production evidence; the pre-commit verification gate is active**. The next independent Essential V1 slice is local LoRA training and dataset preparation.

Remaining V1 work:

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

## Inpainting and variation files changed

- `apps/orchestrator/migrations/007_derivative_results.sql`
- `apps/orchestrator/src/vanta_orchestrator/config.py`
- `apps/orchestrator/src/vanta_orchestrator/schemas.py`
- `apps/orchestrator/src/vanta_orchestrator/engine.py`
- `apps/orchestrator/src/vanta_orchestrator/app.py`
- `apps/orchestrator/tests/test_api.py`
- `apps/orchestrator/tests/test_migrations.py`
- `apps/orchestrator/tests/test_real_generation_domain.py`
- `apps/desktop/src/App.tsx`
- `apps/desktop/src/api.ts`
- `apps/desktop/src/types.ts`
- `apps/desktop/src/styles.css`
- `engine/manifests/core-components.v1.json`
- `scripts/verify_managed_pose.py`
- `docs/ESSENTIAL_V1_RUNBOOK.md`
- `docs/MANUAL_ACCEPTANCE_CHECKLIST.md`

## FLUX adapter files changed

- `apps/orchestrator/src/vanta_orchestrator/engine.py`
- `apps/orchestrator/src/vanta_orchestrator/repositories.py`
- `apps/orchestrator/src/vanta_orchestrator/schemas.py`
- `apps/orchestrator/tests/test_real_generation_domain.py`
- `apps/desktop/src/App.tsx`
- `engine/manifests/model-packs.v1.json`
- `engine/workflows/image-flux-photoreal-v1.json`
- `scripts/verify_managed_pose.py`
- `docs/ESSENTIAL_V1_RUNBOOK.md`
- `docs/MANUAL_ACCEPTANCE_CHECKLIST.md`

## Video and Reference Motion files changed

- `apps/orchestrator/migrations/008_video_motion.sql`
- `apps/orchestrator/src/vanta_orchestrator/video.py`
- `apps/orchestrator/src/vanta_orchestrator/config.py`
- `apps/orchestrator/src/vanta_orchestrator/comfy_runtime.py`
- `apps/orchestrator/src/vanta_orchestrator/schemas.py`
- `apps/orchestrator/src/vanta_orchestrator/engine.py`
- `apps/orchestrator/src/vanta_orchestrator/app.py`
- `apps/orchestrator/pyproject.toml`
- `apps/orchestrator/tests/test_api.py`
- `apps/orchestrator/tests/test_migrations.py`
- `apps/orchestrator/tests/test_real_generation_domain.py`
- `apps/desktop/src-tauri/src/lib.rs`
- `apps/desktop/src/App.tsx`
- `apps/desktop/src/App.test.tsx`
- `apps/desktop/src/api.ts`
- `apps/desktop/src/types.ts`
- `apps/desktop/src/styles.css`
- `engine/manifests/core-components.v1.json`
- `engine/manifests/model-packs.v1.json`
- `engine/workflows/video-ltxv-i2v-v1.json`
- `scripts/build-orchestrator-sidecar.ps1`
- `scripts/verify_managed_pose.py`
- `docs/ESSENTIAL_V1_RUNBOOK.md`
- `docs/MANUAL_ACCEPTANCE_CHECKLIST.md`
- `docs/CAPABILITY_EVIDENCE.md`

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
- Full Python suite after FLUX integration: **27 passed**, 33 deprecation warnings.
- FLUX/SDXL family detection, native graph isolation, safe defaults and LoRA wiring have automated domain coverage.
- Ruff lint, desktop strict TypeScript and ESLint zero-warning gates after FLUX integration: **passed**.
- Desktop Vitest from its package configuration with Vite's non-bundling config loader: **2 files / 2 tests passed**.
- FLUX-integrated production renderer build: **passed**, 1,656 modules, 294.75 kB JS / 42.76 kB CSS before gzip; Tauri `cargo check`: **passed**.
- Inpainting/derivative focused API, migration and workflow suite: **17 passed** before final slice gate.
- Inpainting request test proves the base64 canvas is validated into a Vanta-owned PNG and removed from persisted job JSON.
- Desktop strict TypeScript and focused ESLint after editor integration: **passed**.
- Full Python suite after inpainting/variations: **25 passed**, 33 deprecation warnings.
- Full Ruff lint and format gate: **passed** (16 files formatted).
- Full desktop/domain/UI strict TypeScript and ESLint zero-warning gate: **passed**.
- Desktop Vitest: **2 files / 2 tests passed**.
- Changed-file Prettier and manifest UTF-8 JSON validation: **passed**.
- Desktop production renderer build: **passed**, 1,656 modules, 293.75 kB JS / 42.76 kB CSS before gzip.
- Tauri `cargo check`: **passed**.

- Full Python suite after video and Reference Motion integration: **32 passed**, 37 deprecation warnings.
- Video domain coverage proves native LTXV graph shape, exact 49-frame two-second profile, distilled sigma schedule, playable managed MP4 encoding, identity-safe broad-motion description, rights enforcement, persistence migration and API job routing.
- Desktop strict TypeScript and focused ESLint: **passed**; desktop Vitest with jsdom: **1 passed**.
- Video-integrated renderer production build: **passed**, 1,656 modules, 309.08 kB JS / 45.66 kB CSS before gzip; Tauri `cargo check`: **passed**.

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
- Real current-code inpaint:
  - Source `generation-44d21d6c6860476497f4ef2d711fea59`, job `job-bc1f87bf430045f08badb547bb7bd821`, derivative `generation-2f7a5235250f4e0b855011602a0fb35b`.
  - 832x1216, 25 steps, denoise 0.55, 36.36 seconds, workflow `image-sdxl-inpaint-v1`.
  - Persisted mask SHA-256 `2e5dadc02982cdecb7070a784411383b6f34a1f3850dbb7277c13428a15ea947`; metadata records region prompts, 12-pixel latent mask growth and exact outside-mask compositing.
  - Visual review accepted the coherent cream blouse edit; face, hair, arms, pose, skirt and environment remain unchanged outside the torso mask.
  - Two earlier real integration passes remain honestly persisted: one was rejected for an over-broad mask, and one feathering experiment exposed black-vignette behavior. Automatic feathering was removed; neither rejected artifact is counted as acceptance evidence.
- Real controlled clothing variation:
  - Source `generation-44d21d6c6860476497f4ef2d711fea59`, job `job-a2961eeecfe3428ba20119ae073bce75`, derivative `generation-0bcd65442839465e9b0ac6acce1506d9`.
  - 832x1216, denoise 0.62, 25 steps, 35.94 seconds, workflow `image-sdxl-variation-img2img-v1`, mode `clothing`.
  - Visual review confirms an obvious wardrobe derivative (structured buttoned vest and tailored shorts) while retaining the bedroom composition and original-character appearance.
- Real controlled lighting variation:
  - Source `generation-44d21d6c6860476497f4ef2d711fea59`, job `job-af15c7a5123240fe9ad881991ccc187d`, derivative `generation-21fc2465440e4a6d82850cd22167536b`.
  - 832x1216, denoise 0.38, 25 steps, 35.20 seconds, mode `lighting` with warm rose-gold prompt and preserved composition.
- Real native FLUX verification and generation:
  - The existing `%APPDATA%\\studio.vanta.desktop\\engine\\models\\checkpoints\\flux_dev.safetensors` remains in place at exactly 17,078,891,958 bytes; no copy, rename or model conversion occurred.
  - Header inspection proves a self-contained checkpoint with 780 diffusion tensors, 418 embedded text-encoder tensors and 244 embedded VAE tensors. SHA-256 is `2eda627c8aee140edc77e28ed8dd3c662928ae60f0f960f36824f8862dcbb713`.
  - Native one-step diagnostic passed in pinned ComfyUI `v0.27.0`; model alias `photoreal_max` is persisted Ready with workflow `image-flux-photoreal-v1`.
  - Job `job-8e69168e2afd47078e2fe041a3cb9fb7`, generation `generation-e91bd1d704bf4ebda45d4f44bf1d333d`: 768x1024, 20 steps, guidance 3.5, 77.20 seconds on the detected 12 GB GPU using the hardware-safe offload path.
  - Visual review accepted a coherent original editorial portrait with natural anatomy, controlled studio lighting and no visible text or watermark artifacts. Full reproducibility and AI-disclosure metadata are persisted in Gallery.
  - The Create screen exposes verified model profiles and applies FLUX-safe defaults. SDXL-only identity, pose, variation and inpainting controls are explicitly routed to Balanced instead of being compiled into an invalid FLUX graph.

- Real native local image-to-video:
  - LTX-Video 2B distilled FP8 revision `17037c8743450dc873046790dd96fa805ccfaf8d`, 4,461,695,684 bytes, SHA-256 `d6d8fa8ed3a98346787c2503ac80fb5d7cebcf80e356b79a2ba361fbadf97e15`, is installed and verified under `video_ltx_2b`.
  - T5 XXL FP8 revision `2f74b39c0606dae3b2196d79c18c2a40b71f3250`, 4,893,934,904 bytes, SHA-256 `7d330da4816157540d6bb7838bf63a0f02f573fc48ca4d8de34bb0cbfd514f09`, is installed and registered through native `CLIPLoader` type `ltxv`.
  - Managed MP4 encoding is pinned to imageio-ffmpeg 0.6.0 / FFmpeg 7.1, 87,638,016 bytes, SHA-256 `2ce797a0f88d7f067180338fb227f7b1928ea727bd9a4d7a1d022f7c52af71a3`.
  - Job `job-71bf2fbb460c4f4284cc6bdb290397d7`, generation `generation-c8090eca693848409d33b5e12b73372b`: source `generation-e91bd1d704bf4ebda45d4f44bf1d333d`, 512x768, 8 distilled steps, 49 frames at 24 fps, 2.04-second playable H.264 MP4, 35.34 seconds total.
  - Three-frame visual review accepted coherent posture/camera movement, clean anatomy, stable fictional-subject appearance, and no visible text or watermark.
- Real identity-safe Reference Motion:
  - Owned synthetic motion asset `motion-89a989a461624f62a14b24aee54d4538` trims 0.0–2.0 seconds, crop fit, smoothing 0.5 and strength 0.65.
  - Face-disabled DWPose produced a verified 16-frame / 2.0-second / 8 fps skeleton preview. Metadata explicitly records `face_extraction=false`, `audio_transfer=false`, and `source_branding_transfer=false`; the broad description excludes reference-person identity.
  - Job `job-1e0be3d9d98b41acb197aaffdb57ece0`, generation `generation-ee3f72d06504438da0cff49114d903a3`: 49 frames, 2.04 seconds, 24.09-second render, workflow `video-ltxv-reference-motion-v1`.
  - Three-frame visual review accepted a notably stable face, coat, silhouette and restrained leftward movement. Gallery metadata preserves the source image, motion asset, trim, smoothing, strength, model hashes, FFmpeg hash and AI disclosure.

## Blockers

- None currently.
- Upstream pose and identity packages/models require user license review and acceptance. Manifests preserve exact source, revision, size, hash and license metadata; the UI does not claim redistribution review has occurred.

## Exact next action

Commit the verified video and Reference Motion slice, then implement and prove local LoRA training.

## Final acceptance status

**Not accepted. Essential V1 remains in progress.**
