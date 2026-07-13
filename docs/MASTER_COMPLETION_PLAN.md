# Vanta Master Completion Plan

Last audited: 2026-07-13

## Audit evidence

| Area | Classification | Evidence | Gate / next action |
| --- | --- | --- | --- |
| Desktop lifecycle, loopback auth and CORS | verified complete | `apps/desktop/src-tauri/src/lib.rs`, `test_release_runtime.py`, installed-release `orchestrator.log` at `%APPDATA%/studio.vanta.desktop/logs` shows packaged `OPTIONS 200` and authenticated `GET 200`. | Preserve; include in regression checks. |
| No-console desktop and sidecar | implemented but installed UI verification incomplete | GUI subsystem in `src-tauri/src/main.rs`; sidecar build uses `--noconsole`; Comfy launch uses `CREATE_NO_WINDOW`. NSIS release launched without a tracked console process. | Complete visual and normal/forced-close acceptance in Phase 0. |
| Managed ComfyUI runtime | verified complete outside installed UI | `comfy_runtime.py`, core manifest, bundled `engine/tools/7zr.exe`; verified archive extraction, loopback health and hidden process launch in `build/p0-runtime-4`. | Repeat through installed setup UI. |
| SDXL model import and verification | verified complete outside installed UI | `EngineService.import_model` and `verify_model`; imported checkpoint hash and real diagnostic workflow succeeded. | Repeat through installed setup UI. |
| Image workflow and queue | verified complete outside installed UI | `WorkflowCompiler`, `GenerationService`; real 832x1216 SDXL output, persisted metadata, thumbnail and cancellation evidence in `build/p0-runtime-4`. | Verify installed UI, restart persistence and Generate Similar. |
| Gallery and reproducibility | partially implemented | Persistent images, thumbnails, metadata and similar-request route exist in `app.py`, `engine.py`, `App.tsx`. Open/export/detail actions are incomplete. | Harden Phase 0 metadata/media and defer advanced editing to Phase 4. |
| First-run setup | missing | Existing Engine screen has direct install/import actions but no persisted wizard. | Implement in Phase 0 before later feature work. |
| Characters and preset CRUD | implemented but incomplete | SQLite tables, repositories, routes and React screens exist; current character schema lacks the expanded profile/reference-management fields. | Phase 1 profile/reference work after Phase 0 passes. |
| Recipes and prompt compiler | partially implemented | Recipes, preset selection and deterministic compiler exist; Studio controls are limited and recipe persistence lacks full settings. | Phase 5 after Phase 0–4. |
| LoRA import/application | intentionally deferred | No LoRA model records, workflow loader nodes or UI controls. | Phase 1, then real installed-release acceptance. |
| Identity locking | intentionally deferred | Capability is deliberately `unsupported`; no managed adapter is installed. | Phase 2. |
| Pose control | intentionally deferred | Capability is deliberately `unsupported`; no pose pipeline/library exists. | Phase 3. |
| Inpainting, variations, upscaling | intentionally deferred | No real workflow adapters or derivative model. | Phase 4. |
| Training and video | intentionally deferred | No trainer/video adapter, model packs or jobs. | Phases 6–7. |
| Diagnostics and installer upgrades | partially implemented | Sanitized zip export, logs and NSIS packaging exist. About screen, broad health actions, upgrade/uninstall coverage remain missing. | Phases 9–11. |

## Phase gates

### Phase 0 — harden real image generation (in progress)

1. Add persistent first-run setup state and a truthful in-app wizard for engine installation, model import, verification and diagnostic generation.
2. Make current readiness, storage and failure actions visible from Create and Models & Engine without raw engine details.
3. Add integration coverage for runtime-install recovery, model verification, Gallery persistence, Generate Similar and cancellation.
4. Build and install NSIS, then run the full installed-release acceptance: setup, import, generation, cancellation, restart persistence, similar request, normal cleanup and forced cleanup.
5. Update stale fixture-era documentation and record the resulting checkpoint.

Phase 1 and later are blocked until Phase 0 installed-release acceptance passes. Unsupported capabilities remain disabled and labelled Coming later.

### Later phases — not started

1. Character profiles, reference management and real LoRA import/application.
2. Managed identity locking.
3. Managed pose control and library.
4. Derivative editing, inpainting, variations and upscaling.
5. Complete recipes/presets and Studio controls.
6. Local LoRA training.
7. Optional local video and reference motion.
8. Full component management, diagnostics, polish and upgrade safety.

## Source-control status

The workspace was not a Git repository at audit start. Initialize it and checkpoint the verified current foundation before Phase 0 modifications. Build outputs, local runtime tests, imported models and generated media remain ignored.
