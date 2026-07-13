# Project Vanta

Vanta is a premium, local-only Windows desktop studio for original AI characters. The desktop renderer talks to one loopback FastAPI orchestrator, which owns SQLite data, safe engine manifests, model-pack selection, workflow compilation, and eventually the hidden ComfyUI lifecycle.

Milestone 1 includes the complete application foundation and polished functional screens for Create, Characters, Presets, Gallery, Models & Engine, and Settings. Generation, downloads, and engine installation use deterministic local fixtures; no arbitrary remote code is executed.

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

By default, development data is stored under `data/runtime/` and SQLite uses `data/runtime/vanta.db`. Model-pack targets resolve beneath `data/runtime/models/` in Milestone 1. Production packaging will move these into the per-user application data directory.

Back up the entire studio data directory to preserve user content. Database upgrades are applied through numbered, transactional migrations recorded in `schema_migrations`. Built-in presets seed idempotently and can be restored without overwriting user-owned copies. If the development database becomes unusable, stop Vanta, preserve it for diagnosis, rename `vanta.db`, and restart to create a clean database.

## Safety and rights

Create only original characters and use references you have rights to use. Vanta does not include impersonation, provenance removal, or authenticity-misrepresentation features. Fixture generations carry explicit AI-created disclosure metadata.
