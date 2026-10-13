<#
.SYNOPSIS
  Polaris backend'ini baslatir (127.0.0.1, port PORT ortam degiskeni / backend\.env PORT / 8000).
.PARAMETER NoReload
  --reload kapali (demo sirasinda onerilir: reload whisper modelini yeniden yukler).
  backend\__main__.py varsa bu modda `python -m backend` kullanilir.
.PARAMETER Port
  Dinlenecek port (varsayilan: $env:PORT, sonra backend\.env PORT, sonra 8000).
#>
param(
  [switch]$NoReload,
  [int]$Port = 0
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw ".venv yok. Once: .\scripts\setup.ps1  (veya start-polaris.cmd)" }

$envFile = Join-Path $Root "backend\.env"
if (-not (Test-Path $envFile)) { Write-Host "[!] backend\.env yok; .\scripts\setup.ps1 calistirin." -ForegroundColor Yellow }

function Get-EnvFileValue([string]$name) {
  if (-not (Test-Path $envFile)) { return "" }
  $line = Select-String -Path $envFile -Pattern "^\s*$name\s*=" | Select-Object -First 1
  if (-not $line) { return "" }
  $v = ($line.Line -split '=', 2)[1] -replace '\s+#.*$', ''
  return $v.Trim().Trim('"').Trim("'")
}

if ($Port -le 0) {
  if ($env:PORT) { $Port = [int]$env:PORT }
  else {
    $fromFile = Get-EnvFileValue "PORT"
    $Port = if ($fromFile -match '^\d+$') { [int]$fromFile } else { 8000 }
  }
}
$BindHost = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }
$env:PORT = "$Port"
$env:HOST = $BindHost

# Port dolu mu?
$busy = $null
try { $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1 } catch { $busy = $null }
if ($busy) {
  $procName = (Get-Process -Id $busy.OwningProcess -ErrorAction SilentlyContinue).ProcessName
  Write-Host "[HATA] Port $Port dolu (PID $($busy.OwningProcess) $procName)." -ForegroundColor Red
  Write-Host "  - Eski Polaris/uvicorn penceresini kapatin, ya da: Stop-Process -Id $($busy.OwningProcess)"
  Write-Host "  - Ya da baska port: .\scripts\run-backend.ps1 -Port 8010  (veya backend\.env icine PORT=8010)"
  Write-Host "    Sonra panel Ayarlar -> Backend adresi: http://localhost:8010"
  exit 1
}

$useMain = (Test-Path (Join-Path $Root "backend\__main__.py")) -and $NoReload
if ($useMain) {
  $pyArgs = @("-m", "backend", "--host", $BindHost, "--port", "$Port")
} else {
  $pyArgs = @("-m", "uvicorn", "backend.main:app", "--host", $BindHost, "--port", "$Port")
  if (-not $NoReload) { $pyArgs += "--reload" }
}
Write-Host "Polaris backend: http://127.0.0.1:$Port/health  (durdurmak icin Ctrl+C)" -ForegroundColor Cyan
& $py @pyArgs
exit $LASTEXITCODE
