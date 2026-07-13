param(
  [string]$Python = $env:VANTA_PYTHON
)

$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $root

if (-not $Python) {
  $venvPython = Join-Path $root '.venv\Scripts\python.exe'
  if (Test-Path -LiteralPath $venvPython) { $Python = $venvPython }
  else { throw 'Python 3.11 environment not found. Follow the First setup section in README.md or set VANTA_PYTHON.' }
}

$env:VANTA_HOST = '127.0.0.1'
$env:VANTA_PORT = '47831'
$env:PYTHONPATH = (Resolve-Path '.\apps\orchestrator\src').Path
$orchestrator = Start-Process -FilePath $Python -ArgumentList '-m', 'vanta_orchestrator.main' -WorkingDirectory $root -WindowStyle Hidden -PassThru

try {
  $ready = $false
  foreach ($attempt in 1..30) {
    try {
      $health = Invoke-RestMethod -Uri 'http://127.0.0.1:47831/api/health' -TimeoutSec 1
      if ($health.status -eq 'ready') { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 250 }
  }
  if (-not $ready) { throw 'The Vanta orchestrator did not become ready on 127.0.0.1:47831.' }
  pnpm --filter '@vanta/desktop' tauri dev
} finally {
  if ($orchestrator -and -not $orchestrator.HasExited) { Stop-Process -Id $orchestrator.Id }
}
