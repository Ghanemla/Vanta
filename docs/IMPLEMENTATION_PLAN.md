# Milestone 1 Implementation Plan

## Product decisions

- Treat `frontend/design-prototype.html` and the product specifications as the art-direction source of truth: near-black editorial surfaces, warm off-white type, restrained magenta for primary interaction, muted rose and deep plum for secondary emphasis, and distinct semantic colors for ready, warning, and failure states.
- Use system fonts only. No CDN, analytics, authentication, remote inference, or telemetry dependencies are permitted.
- Keep the desktop renderer coupled only to the typed orchestrator API. Engine-specific identifiers and workflow graphs remain inside the Python service.
- Use fixture-backed local SQLite data and deterministic mock installers for Milestone 1. Mock operations never execute downloaded code.
- Use stable model aliases (`photoreal_balanced`, `photoreal_max`, `preview_fast`) and capability names in normal UI language.

## Phases

1. **Workspace foundation**
   - Create the pnpm workspace, shared TypeScript configuration, formatting/linting scripts, Tauri/Vite desktop package, Python package, and Windows scripts.
2. **Domain and UI foundations**
   - Add strict domain types, runtime validation, prompt compilation, preset-copy rules, engine state transitions, recommendation logic, semantic design tokens, and reusable controls.
3. **Local data and orchestration**
   - Add SQLite migrations, fixture seeding, repositories, typed API schemas, local-only FastAPI routes, component/model manifests, adapters, mock progress/cancellation, and workflow compiler boundary.
4. **Milestone screens**
   - Build Create, Characters, Presets, Gallery, Models & Engine, and Settings with loading, empty, success, and failure treatments.
5. **Quality and documentation**
   - Add unit, API, and migration tests; run formatting, linting, type checks, Python tests, frontend tests, Vite build, and Tauri checks/build where local prerequisites permit.

## Key risks and mitigations

- **Native toolchain availability:** Tauri needs Rust and Windows WebView2. Keep the web application independently runnable and record any machine prerequisite precisely.
- **Python availability:** The host Python launcher is absent, so development commands support an explicit `VANTA_PYTHON`; Codex verification uses its bundled Python 3.11 runtime without installing system software.
- **Dependency downloads:** Pin package ranges and lock dependencies after install. The runtime remains fully local; development package downloads are not product network dependencies.
- **Manifest safety:** Validate every manifest before it enters service state, allow only declared mock/local strategies in Milestone 1, and never run manifest-provided shell commands.
- **User data upgrades:** Apply numbered migrations transactionally, track them in `schema_migrations`, and seed built-ins idempotently.
- **Visual drift:** Centralize tokens in `packages/ui`, retain the prototype's restrained palette and asymmetric editorial composition, and avoid generic dashboard component kits.

## Verification commands

```powershell
pnpm install
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm tauri:check
pnpm tauri:build

$env:PYTHONPATH = "apps/orchestrator/src"
python -m pytest apps/orchestrator/tests -q
python -m ruff check apps/orchestrator
python -m ruff format --check apps/orchestrator
```

For local development, run `scripts/dev.ps1`; it starts the orchestrator on `127.0.0.1:47831` and the Tauri/Vite application without exposing either service to the LAN.
