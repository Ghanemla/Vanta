# AGENTS.md — Project Vanta

## Mission

Build a premium, local-only desktop application for creating and managing original AI characters, images, motion references, and LoRA training.

## Non-negotiable product principles

1. Simplicity first.
2. Output quality second.
3. Advanced control is available but never required.
4. No inference API dependency, subscriptions, telemetry, or cloud requirement.
5. The visual design must feel authored and premium, not like a generic dashboard template.
6. ComfyUI is an internal engine. Normal users never need to open its UI.
7. Never silently expose services beyond 127.0.0.1.
8. All starter presets are editable through user-owned copies; built-ins remain restorable.
9. Public-facing synthetic content should support clear AI disclosure metadata.

## Intended architecture

- Tauri desktop shell.
- React + TypeScript frontend.
- Local Python/FastAPI orchestration service.
- SQLite for characters, presets, recipes, jobs, generations, datasets and training runs.
- ComfyUI launched and supervised as a hidden child process.
- Adapter interfaces for generation engines and training engines.
- No direct dependence of UI components on ComfyUI node IDs.
- Versioned internal workflow templates compiled into ComfyUI API workflow JSON.

## Visual direction

Dark editorial workspace, restrained color, large typography, tactile panels, controlled gradients, no excessive glassmorphism, no neon cyberpunk clichés, no rainbow AI branding. Accent is editorial magenta used sparingly with deep plum and muted rose supporting tones. Semantic success, warning, and error colors remain distinct from the brand accent. Motion should be subtle and functional.

Use `frontend/design-prototype.html` as the initial art-direction reference, not as final production code.

## Implementation order

1. Monorepo scaffold and developer scripts.
2. Design tokens and application shell.
3. SQLite schema and migrations.
4. Preset CRUD, built-in reset, tags, favorites, import/export.
5. Character CRUD and identity assets.
6. Recipe composer and prompt compiler.
7. Engine supervisor and ComfyUI health check.
8. Versioned image workflow compiler and job queue.
9. Gallery and reproducible metadata.
10. Dataset manager and training adapter.
11. Pose library.
12. Video/motion adapter.

## Definition of done for each feature

- Has typed domain models.
- Has loading, empty, success and failure states.
- Works without internet.
- Has automated tests for domain logic.
- Does not require editing JSON manually.
- Documents local paths and recovery behavior.
- Preserves user data across upgrades.

## Safety and rights

Support only original characters or references the user has rights to use. Do not build features intended to impersonate real people, bypass consent, remove provenance, or misrepresent generated media as authentic footage.

## Installation and model policy

- Required capabilities are installed and repaired through Vanta component manifests.
- Users see capability names, never raw missing-node instructions in normal UI.
- Large models are selectable, verified model packs and are not blindly bundled.
- Reference model profiles through stable aliases, never hard-coded checkpoint filenames.
- The default profile is `photoreal_balanced`; model choice is changed in Models & Engine settings.
- Preserve license, source, version and hash metadata for every distributable dependency.
