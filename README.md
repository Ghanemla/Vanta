# Project Vanta

Vanta is a premium, local-only Windows desktop studio for original AI characters. The desktop renderer talks to one authenticated loopback FastAPI orchestrator, which owns SQLite data, safe engine manifests, model-pack selection, workflow compilation, and the hidden ComfyUI lifecycle.

V1.0.1 includes real local SDXL and FLUX image generation, identity and pose controls, inpainting, variations, upscaling, image-to-video, Reference Motion, LoRA training, persistent recipes, and reproducible Gallery metadata. Media is loaded through typed authenticated endpoints and a shared object-URL cache; raw local paths and launch tokens never become browser URLs. No inference API, account, telemetry, or arbitrary remote-code path is present.

Generation progress is persisted across navigation and restart with honest engine, model, sampling, decoding, saving, and finalization states. Video defaults to a verified two-second Safe profile, exposes a four-second Standard profile, keeps unverified 6–8 second single-pass rendering disabled, and provides a persisted multi-segment sequence workflow for longer work.

## Prerequisites on Windows

- Node.js 22 or newer
- pnpm 11 (`corepack enable`, then `corepack prepare pnpm@11.7.0 --activate` if needed)
- Python 3.11, including `venv`
- Rust stable with the MSVC target
- Microsoft C++ Build Tools and Windows 10/11 SDK
- WebView2 Runtime (normally included with current Windows)

No model, CUDA toolkit, ComfyUI installation, account, API key, web font, or internet connection is needed to run Milestone 1 after development dependencies are installed.

## First setup

From PowerShell in this directory:

```powershell
pnpm install
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\apps\orchestrator[dev]"
Copy-Item .env.example .env
```

The environment file contains no secrets. Keep `VANTA_HOST=127.0.0.1`; the service intentionally rejects non-loopback binding.

## Run the desktop application

The convenience script launches the orchestrator as a hidden child process, waits for its health check, then runs Tauri:

```powershell
.\scripts\dev.ps1
```

To run each process separately for debugging:

```powershell
$env:PYTHONPATH = (Resolve-Path ".\apps\orchestrator\src").Path
.\.venv\Scripts\python.exe -m vanta_orchestrator.main
```

In another PowerShell window:

```powershell
pnpm --filter @vanta/desktop tauri dev
```

The web renderer can also be inspected at `http://127.0.0.1:1420` while Vite is running. The API docs are local at `http://127.0.0.1:47831/api/docs`.

## Quality commands

```powershell
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm tauri:check
pnpm tauri:build
pnpm tauri:bundle

$env:PYTHONPATH = (Resolve-Path ".\apps\orchestrator\src").Path
.\.venv\Scripts\python.exe -m ruff format --check apps/orchestrator
.\.venv\Scripts\python.exe -m ruff check apps/orchestrator
.\.venv\Scripts\python.exe -m pytest apps/orchestrator/tests -q
```

Run `.\scripts\diagnose.ps1` to verify tool versions, loopback configuration, local paths, manifests, and API health.

## Workspace map

- `apps/desktop` — Tauri 2 shell and React/Vite renderer
- `apps/orchestrator` — FastAPI service, SQLite migrations, repositories, and engine boundaries
- `packages/domain` — shared TypeScript types, runtime validation, and domain rules
- `packages/ui` — Vanta design tokens and authored UI primitives
- `engine/manifests` — versioned core-component and model-pack metadata
- `engine/workflows` — versioned, Vanta-owned workflow templates hidden from the UI
- `data/starter_presets.json` — recoverable built-in preset source
- `scripts` — Windows development and diagnostic commands

## Local paths and recovery

By default, development data is stored under `data/runtime/` and SQLite uses `data/runtime/vanta.db`. Production initially uses `%APPDATA%\studio.vanta.desktop`. Settings can safely relocate the complete studio-data root (including SQLite, managed runtime, models, media, training, logs, and diagnostics) to a local folder such as `F:\VantaData`. Vanta copies and verifies the destination, restarts its managed local service against it, and keeps the original until the user removes it manually. A small bootstrap record outside studio storage preserves the selected root across upgrades and Repair Installation. Application binaries and studio data are separate.

Gallery details offer Open file, Show in folder, Save a copy, and Copy file path. These are typed native actions: Vanta resolves the selected record inside its owned storage before Windows opens, reveals, or copies it. Exports always copy the original and never move Vanta-managed media.

Back up the entire studio data directory to preserve user content. Database upgrades are applied through numbered, transactional migrations recorded in `schema_migrations`. Built-in presets seed idempotently and can be restored without overwriting user-owned copies. Settings includes a non-destructive media repair that validates originals, normalizes owned paths, restores missing derivatives and reports truly missing files without fabricating or deleting media. If a database becomes unusable, stop Vanta, preserve it for diagnosis, rename `vanta.db`, and restart to create a clean database.

## Safety and rights

Create only original characters and use references you have rights to use. Vanta does not include impersonation, provenance removal, or authenticity-misrepresentation features. Fixture generations carry explicit AI-created disclosure metadata.
