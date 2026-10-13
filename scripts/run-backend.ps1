<#
.SYNOPSIS
  Polaris backend'ini baslatir (venv aktif, uvicorn --reload, 127.0.0.1:8000).
.PARAMETER NoReload
  --reload kapali (demo sirasinda onerilir: reload whisper modelini yeniden yukler).
#>
param([switch]$NoReload)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$act = Join-Path $Root ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $act)) { throw ".venv yok. Once: .\scripts\setup.ps1" }
. $act

if (-not (Test-Path "backend\.env")) { Write-Host "[!] backend\.env yok; .\scripts\setup.ps1 calistirin." -ForegroundColor Yellow }

$uvArgs = @("-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000")
if (-not $NoReload) { $uvArgs += "--reload" }
Write-Host "Polaris backend: http://127.0.0.1:8000/health  (durdurmak icin Ctrl+C)" -ForegroundColor Cyan
python @uvArgs
