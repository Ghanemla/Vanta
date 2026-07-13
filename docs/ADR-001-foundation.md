# ADR-001: Local application foundation

## Status

Accepted for Milestone 1.

## Context

Vanta must present one coherent desktop studio while isolating a changing set of local generation and training engines. User data must survive application upgrades, and the product cannot require cloud infrastructure.

## Decision

Use a pnpm monorepo with a Tauri 2 lifecycle owner, React/TypeScript renderer, Python 3.11 FastAPI orchestrator, and SQLite. The renderer communicates only with the orchestrator through typed HTTP contracts. The orchestrator binds to `127.0.0.1`, owns persistence and lifecycle, and translates stable domain requests through adapter interfaces.

The desktop UI never constructs ComfyUI graphs or refers to node IDs. Versioned workflow templates remain an internal orchestrator asset. TypeScript domain rules are shared between UI packages; Python request schemas independently validate the process boundary.

## Consequences

- Development requires both Node/Rust and Python toolchains.
- The process boundary makes lifecycle and error handling explicit, while keeping engine package churn out of React.
- SQLite numbered migrations and idempotent built-in seeding preserve user data and restore product defaults.
- Tauri owns production child-process supervision; Milestone 1's PowerShell script provides an equivalent development lifecycle.
