You are starting Milestone 1 of Project Vanta.

Before doing anything:

1. Read `AGENTS.md`.
2. Read `docs/PRODUCT_SPEC.md`.
3. Read `docs/ENGINE_PACKS.md`.
4. Inspect `frontend/design-prototype.html`.
5. Create `docs/IMPLEMENTATION_PLAN.md` with phases, risks, and verification commands, then begin implementation. Do not stop after planning.

## Product goal

Build a premium, fully local Windows desktop application for creating and managing original AI characters. ComfyUI is an internal headless generation engine. Normal users must never need to open ComfyUI, connect nodes, edit JSON, browse model folders, or diagnose a missing node.

There must be no paid inference APIs, cloud requirement, analytics, login, telemetry, CDN assets, or web fonts.

## Architecture to implement

Create a pnpm monorepo with:

- `apps/desktop`: Tauri 2, React, TypeScript and Vite
- `apps/orchestrator`: Python 3.11 FastAPI local service
- `packages/domain`: shared TypeScript domain types and validation
- `packages/ui`: design tokens and reusable authored UI components
- `engine/manifests`: versioned component and model-pack manifests
- `engine/workflows`: versioned Vanta-owned workflow templates
- `scripts`: Windows development and diagnostic scripts

Use SQLite with migrations. Bind all services to `127.0.0.1` only.

The frontend talks only to the orchestrator. It must not know ComfyUI node IDs or construct raw node graphs. Put all engine-specific behavior behind typed adapters and a workflow compiler.

## Required installation behavior

Implement the foundation for two distinct dependency types.

### A. Core engine components

These are required capabilities such as Pose Control, identity support, preprocessing, inpainting support, image I/O and workflow execution.

Represent them with a versioned component manifest containing:

- id
- display name
- version or pinned revision
- source
- license metadata
- install strategy
- health checks
- repair strategy
- dependencies
- provided capabilities

The application must support these states:

- not installed
- installing
- ready
- update available
- repair needed
- unsupported

Normal UI language must say things such as “Install Pose Control” or “Repair Identity Lock,” not “install this node from GitHub.”

For Milestone 1, implement the manifest parser, state machine, mock installer, progress events, cancellation and health-check interfaces. Do not execute arbitrary remote scripts yet.

### B. Model packs

Large model files are optional/selectable packs and must have:

- stable internal id
- display name
- model family
- capabilities
- hardware recommendation
- expected VRAM and disk usage
- download source metadata
- license name and acceptance requirements
- SHA-256 hash field
- target path
- installed and verified state
- compatible workflow profiles
- active/default flag

Implement Install, Pause/Resume, Verify, Repair, Remove and Set Default as typed services with a mock/local fixture implementation in Milestone 1.

Do not hard-code the app to a single checkpoint filename. Use aliases such as:

- `photoreal_balanced`
- `photoreal_max`
- `preview_fast`

The initial product default is `photoreal_balanced`, targeting a dependable SDXL-compatible photoreal workflow on a 12 GB GPU. Maximum-quality FLUX-compatible support is optional and must not be the only required path.

## Milestone 1 screens

Implement these visually polished, working screens:

1. Create
   - Simple and Studio mode
   - character, wardrobe, expression, pose, location, lighting, camera and quality profile
   - freeform prompt direction
   - custom tags
   - recipe save
   - engine readiness and missing-capability call to action

2. Characters
   - create, edit and archive character profiles
   - identity description and reference asset placeholders
   - assigned default recipe/model profile

3. Presets
   - categories and search
   - create, edit, duplicate, favorite, tag and delete user presets
   - editing a built-in creates a user-owned copy
   - restore built-ins
   - import/export JSON

4. Gallery
   - local fixture generations
   - filters
   - metadata drawer
   - Generate Similar action placeholder

5. Models & Engine
   - core capability cards
   - model-pack cards
   - Ready/Install/Repair/Verify/Switch states
   - download/progress mock
   - recommended badge based on a mocked RTX 4070 Super 12 GB hardware profile
   - diagnostics drawer with human-readable messages and raw logs separated

6. Settings
   - local paths
   - storage
   - Simple/Studio default
   - engine startup preference
   - privacy statement
   - no cloud/API controls because the product is local-only

## Database

Create migrations and repositories for:

- characters
- presets
- recipes
- recipe_items
- engine_components
- model_packs
- generation_jobs
- generations
- app_settings

Seed presets from `data/starter_presets.json`.

## Design quality

Use `frontend/design-prototype.html` as art direction, not final code.

Requirements:

- dark editorial workspace
- highly controlled editorial magenta accent with deep plum and muted rose supporting tones
- strong typography and spacing hierarchy
- tactile panels
- subtle functional animation
- no generic admin dashboard
- no default shadcn look
- no excessive glassmorphism
- no neon cyberpunk clichés
- no gradient-filled AI icons everywhere
- responsive down to a practical laptop width
- accessible focus states and keyboard navigation

Use local system fonts only for now.

## Testing and quality

Implement:

- strict TypeScript
- formatting and linting
- unit tests for preset-copy rules, prompt compilation, component state transitions, model-pack selection and hardware recommendations
- API tests for CRUD endpoints
- migration tests
- loading, empty, success and failure states
- no manually edited JSON required by users

Run every available formatter, type checker, test and build command. Fix failures rather than merely reporting them.

## Documentation

Create:

- `README.md` with exact Windows development steps
- `docs/ADR-001-foundation.md`
- `docs/ADR-002-engine-manifests.md`
- `docs/LOCAL_SECURITY.md`
- `.env.example` containing no secrets

## Completion report

At the end, report:

- architecture created
- exact commands run
- tests and builds passed
- files changed
- how to launch the desktop and orchestrator
- remaining local prerequisites
- what is mocked versus functional
- concise visual description of every screen
- next milestone recommendation

Do not redesign the product direction. Do not implement paid API fallbacks. Do not stop to ask questions unless a truly blocking repository fact cannot be inferred; make reasonable documented choices and continue.
