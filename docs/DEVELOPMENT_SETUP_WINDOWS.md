# Windows development setup

Use Windows 11, Git, Node.js 22+, pnpm 11.7.0, Python 3.11+, Rust stable MSVC, WebView2, and Visual Studio Build Tools 2022 with **Desktop development with C++**, MSVC v143, CMake tools, and the Windows 11 SDK. GitHub CLI is required only to publish.

Keep the repository, `.venv`, `node_modules`, Rust target, build output, and caches on any local drive with sufficient space. A non-system build drive is convenient but not required. The installed application accepts any suitable writable local studio root; release acceptance for 0.1.3 uses `F:\VantaAcceptance\0.1.3` only to isolate disposable evidence on this test machine.

Run from PowerShell:

```powershell
.\scripts\check-windows-prerequisites.ps1
.\scripts\setup-windows-development.ps1
pnpm.cmd tauri:bundle
```

If execution policy blocks a script, run `Set-ExecutionPolicy -Scope Process Bypass` or invoke the script with `powershell -ExecutionPolicy Bypass -File ...`. Use `pnpm.cmd` when PowerShell's `pnpm.ps1` is blocked. `corepack` EPERM errors are resolved by using the existing pnpm installation rather than installing global tooling.

The sidecar must exist at `apps\desktop\src-tauri\binaries\vanta-orchestrator-x86_64-pc-windows-msvc.exe` before Tauri checks or bundling. The bundle is unsigned unless a valid code-signing certificate is supplied, so Windows shows an unknown-publisher warning. Build source, then use `gh auth login`, push `main`, tag, and create a release with the NSIS installer and checksum.
