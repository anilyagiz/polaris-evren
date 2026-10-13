<#
.SYNOPSIS
  Extension'i derler (pnpm build) ve Brave'e yukleme adimlarini yazdirir.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Ext = Join-Path $Root "extension"
$usePnpm = [bool](Get-Command pnpm -ErrorAction SilentlyContinue)
Push-Location $Ext
try {
  if (-not (Test-Path "node_modules")) {
    if ($usePnpm) { pnpm install } else { npm install --no-package-lock --no-audit --no-fund }
    if ($LASTEXITCODE -ne 0) { throw "bagimlilik kurulumu basarisiz (pnpm EPERM verirse: npm install --no-package-lock)" }
  }
  if ($usePnpm) { pnpm build } else { npm run build }
  if ($LASTEXITCODE -ne 0) { throw "derleme basarisiz" }
} finally { Pop-Location }

$dist = Join-Path $Ext "dist"
Write-Host ""
Write-Host "Derleme tamam: $dist" -ForegroundColor Green
Write-Host @"

Brave'e yukleme:
  1) Brave'de brave://extensions adresini acin
  2) Sag ustte 'Gelistirici modu'nu acin
  3) 'Paketlenmemis uzantiyi yukle' -> su klasoru secin: $dist
  4) Polaris simgesine tiklayin / yan paneli acin
  5) Ilk bas-konus'ta mikrofon iznini verin (brave://settings/content/microphone)
  6) Panel Ayarlar -> Backend adresi: http://localhost:8000
Kod degisikliginden sonra bu betigi tekrar calistirin, brave://extensions'ta uzantiyi yenileyin.
"@
