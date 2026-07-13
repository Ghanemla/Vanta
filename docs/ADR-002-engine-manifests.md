# ADR-002: Engine components and model packs are distinct

## Status

Accepted for Milestone 1.

## Context

Core capabilities need reviewed, pinned application-managed dependencies. Multi-gigabyte models have different licenses, hardware requirements, distribution rules, and user choice. Treating both as anonymous ComfyUI nodes or bundled files would create unsafe repairs and poor license handling.

## Decision

Maintain two versioned manifest families:

- Core component manifests declare display name, revision, source, license review state, safe install strategy, health checks, repair strategy, dependencies, and capabilities.
- Model-pack manifests declare stable alias, family, capabilities, RAM/VRAM/disk fit, source/authentication metadata, license acceptance, SHA-256, target path, compatible workflows, and default state.

Normal UI uses capability language. Engine identifiers and logs are shown only in diagnostics. Model references use aliases such as `photoreal_balanced`, never a community checkpoint filename.

Milestone 1 parsers reject unknown fields and unsupported install strategies. The installer is deterministic and local; it emits progress, supports cancellation/pause/resume, and records state without downloading or executing anything. Real download/install adapters must later enforce an allowlist, TLS, hash validation, path containment, license acceptance, resumable transfers, and atomic activation.

## Consequences

- Model licensing remains explicit and reviewable before distribution.
- Application updates can repair required capabilities without silently downloading optional models.
- Hardware recommendation rules can evolve independently from UI labels and engine implementations.
