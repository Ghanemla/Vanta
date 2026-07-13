param(
  [string]$Python = (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe')
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = (Resolve-Path $Python).Path
$dist = Join-Path $root 'build\orchestrator-sidecar'
$work = Join-Path $root 'build\orchestrator-work'
$spec = Join-Path $root 'build\orchestrator-spec'
$target = Join-Path $root 'apps\desktop\src-tauri\binaries\vanta-orchestrator-x86_64-pc-windows-msvc.exe'

New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
& $pythonPath -m PyInstaller --noconfirm --clean --onefile --noconsole --name vanta-orchestrator `
  --paths (Join-Path $root 'apps\orchestrator\src') `
  --add-data "$(Join-Path $root 'apps\orchestrator\migrations');migrations" `
  --add-data "$(Join-Path $root 'data\starter_presets.json');data" `
  --add-data "$(Join-Path $root 'engine');engine" `
  --distpath $dist --workpath $work --specpath $spec `
  (Join-Path $root 'apps\orchestrator\sidecar_entry.py')

Copy-Item -LiteralPath (Join-Path $dist 'vanta-orchestrator.exe') -Destination $target -Force
Write-Output "Built Vanta orchestrator sidecar: $target"
