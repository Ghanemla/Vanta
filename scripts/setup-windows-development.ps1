param([string]$Repository = $PSScriptRoot + '\..')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path $Repository).Path
& (Join-Path $root 'scripts\check-windows-prerequisites.ps1') -Repository $root
Push-Location $root
try {
  pnpm.cmd install --frozen-lockfile
  if (-not (Test-Path '.venv\Scripts\python.exe')) { py -3.11 -m venv .venv }
  .\.venv\Scripts\python.exe -m pip install --upgrade pip
  .\.venv\Scripts\python.exe -m pip install -e '.\apps\orchestrator[dev]'
  if (-not (Test-Path '.env')) { Copy-Item '.env.example' '.env' }
  pnpm.cmd sidecar:build
  $sidecar = 'apps\desktop\src-tauri\binaries\vanta-orchestrator-x86_64-pc-windows-msvc.exe'
  if (-not (Test-Path $sidecar)) { throw "Sidecar build did not produce $sidecar" }
  .\.venv\Scripts\python.exe -m vanta_orchestrator.main --self-test
  pnpm.cmd tauri:check
  Write-Host 'Development setup completed. If PowerShell blocks pnpm, use pnpm.cmd as shown.'
} finally { Pop-Location }
