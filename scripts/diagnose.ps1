$ErrorActionPreference = 'Continue'
$root = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $root

Write-Output 'Vanta local diagnostics'
Write-Output "Workspace: $root"
Write-Output "Node: $(node --version)"
Write-Output "pnpm: $(pnpm --version)"
Write-Output "Rust: $(rustc --version)"

$python = if ($env:VANTA_PYTHON) { $env:VANTA_PYTHON } elseif (Test-Path '.\.venv\Scripts\python.exe') { (Resolve-Path '.\.venv\Scripts\python.exe').Path } else { $null }
if ($python) { Write-Output "Python: $(& $python --version)" } else { Write-Warning 'No configured Python environment found.' }

Get-ChildItem -LiteralPath '.\engine\manifests' -Filter '*.json' | ForEach-Object {
  try { Get-Content -Raw -LiteralPath $_.FullName | ConvertFrom-Json | Out-Null; Write-Output "Manifest OK: $($_.Name)" }
  catch { Write-Warning "Manifest invalid: $($_.Name)" }
}

try {
  $health = Invoke-RestMethod -Uri 'http://127.0.0.1:47831/api/health' -TimeoutSec 2
  Write-Output "Orchestrator: $($health.status) on $($health.host)"
} catch { Write-Output 'Orchestrator: not running' }
