param([string]$Repository = $PSScriptRoot + '\..')

$ErrorActionPreference = 'Continue'
$missing = @()
function Check-Command([string]$Name, [switch]$Required) {
  $found = Get-Command $Name -ErrorAction SilentlyContinue
  if ($found) { Write-Host "[ok] $Name $(& $Name --version 2>$null | Select-Object -First 1)" }
  else { Write-Host "[missing] $Name" -ForegroundColor Red; if ($Required) { $script:missing += $Name } }
}
Check-Command git -Required
Check-Command gh
if (Get-Command gh -ErrorAction SilentlyContinue) { if (gh auth status 2>$null) { Write-Host '[ok] GitHub authentication' } else { Write-Host '[info] GitHub authentication is needed only to publish.' } }
Check-Command node -Required; Check-Command npm -Required; Check-Command pnpm -Required
Check-Command py -Required; Check-Command rustup -Required; Check-Command rustc -Required; Check-Command cargo -Required
try { $python = & py -3.11 --version 2>$null; Write-Host "[ok] $python" } catch { $missing += 'Python 3.11' }
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) { Write-Host '[ok] Visual Studio Installer / Build Tools found' } else { Write-Host '[missing] Visual Studio Build Tools 2022' -ForegroundColor Red; $missing += 'Visual Studio Build Tools 2022' }
if (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\*' -ErrorAction SilentlyContinue | Where-Object { $_.name -match 'WebView2' }) { Write-Host '[ok] WebView2 Runtime' } else { Write-Host '[missing] WebView2 Runtime' -ForegroundColor Red; $missing += 'WebView2 Runtime' }
$root = (Resolve-Path $Repository).Path; $drive = (Get-Item $root).PSDrive
Write-Host "[info] Repository drive: $($drive.Name): ($([math]::Round($drive.Free / 1GB, 1)) GB free)"
try { Write-Host "[info] pnpm store: $(pnpm store path 2>$null)" } catch {}
try { Write-Host "[info] npm cache: $(npm config get cache 2>$null)" } catch {}
try { Write-Host "[info] pip cache: $(py -3.11 -m pip cache dir 2>$null)" } catch {}
if ($missing.Count) { Write-Error "Mandatory prerequisites missing: $($missing -join ', ')"; exit 1 }
