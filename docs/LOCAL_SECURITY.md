# Local security model

## Network boundary

Vanta's orchestrator binds only to IPv4 loopback `127.0.0.1`. Configuration rejects any other host rather than silently exposing the service. Vite also binds to loopback in development. The Tauri content security policy permits connections only to the packaged origin and `http://127.0.0.1:47831`.

Milestone 1 contains no analytics, telemetry, login, remote inference, CDN asset, web-font, or cloud-storage integration.

## Process and engine boundary

The renderer cannot launch commands or load engine graphs. It requests typed capabilities from the orchestrator. Core component manifests define fixed Vanta-owned installation behavior only: the reviewed ComfyUI archive and its bundled, hash-verified archive extractor are installed by code, never by instructions fetched from a remote manifest. Full technical logs are separated from user-facing repair language.

Managed installers use fixed Vanta code paths rather than commands embedded in a manifest. They validate download origin, size, SHA-256, archive path containment, extractor hash, runtime layout and loopback health before activation. ComfyUI remains a hidden Vanta-owned child process on loopback; the Windows Job Object owns the orchestrator tree and uses kill-on-close cleanup.

## Data and recovery

SQLite uses foreign keys and transactional migrations. User data and built-in origins are distinct; built-in edits create user-owned copies. Back up the configured studio-data directory to preserve characters, presets, recipes, generations, and future training assets.

Development paths are relative to the repository by default. Production packaging must use a per-user application data directory with user-only filesystem permissions. Exported preset JSON is schema-validated on import; users never need to edit it manually.

## Content rights and disclosure

The product is limited to original characters and reference assets the user has rights to use. It must not add impersonation, consent bypass, provenance removal, or authentic-footage misrepresentation workflows. Public-facing generated media should carry AI disclosure metadata; Milestone 1 gallery fixtures demonstrate this field.
