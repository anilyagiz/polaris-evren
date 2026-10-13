<#
.SYNOPSIS
  Extension'i npm ile derler (extension\dist) ve Brave'e yukleme adimlarini yazdirir.
  Not: pnpm bu projede kullanilmiyor (Windows'ta esbuild icin EPERM veriyor).
.PARAMETER Install
  node_modules olsa bile npm install calistirir.
.PARAMETER Quiet
  Brave yukleme adimlarini yazdirmaz.
#>
param([switch]$Install, [switch]$Quiet)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Ext = Join-Path $Root "extension"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "npm bulunamadi. Node.js 18+ kurun: https://nodejs.org" }
Push-Location $Ext
try {
  if (Test-Path "node_modules\.pnpm") {
    Write-Host "[!] pnpm ile kurulmus node_modules bulundu; npm icin temizleniyor" -ForegroundColor Yellow
    Remove-Item -Recurse -Force node_modules
  }
  if ($Install -or -not (Test-Path "node_modules")) {
    npm install --no-package-lock --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm install basarisiz (node_modules klasorunu silip tekrar deneyin)" }
  }
  npm run build
  if ($LASTEXITCODE -ne 0) { throw "derleme basarisiz (npm run build)" }
} finally { Pop-Location }

$dist = Join-Path $Ext "dist"
Write-Host ""
Write-Host "Derleme tamam: $dist" -ForegroundColor Green
if ($Quiet) { return }
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
