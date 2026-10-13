<#
.SYNOPSIS
  Polaris surum paketi uretir:
    release\polaris-v<surum>.zip            (calistirilabilir proje: extension-dist + backend + scripts + ...)
    release\polaris-extension-v<surum>.zip  (yalnizca derlenmis extension, Brave'e "yukle" icin)
    release\PolarisSetup-<surum>.exe        (Windows kurulum sihirbazi - kullaniciya onerilen yol; installer\)
  Surum extension\manifest.json "version" alanindan okunur.
.PARAMETER SkipBuild
  extension\dist'i yeniden derlemez (var olani kullanir).
.PARAMETER SkipTray
  tray\build.ps1 calistirilmaz (var olan tray\dist\Polaris.exe varsa yine eklenir).
.PARAMETER NoInstaller
  PolarisSetup-<surum>.exe uretilmez (Inno Setup gerektirmeden yalnizca zip'ler).
#>
param([switch]$SkipBuild, [switch]$SkipTray, [switch]$NoInstaller)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$manifest = Get-Content (Join-Path $Root "extension\manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$version = $manifest.version
if (-not $version) { throw "extension\manifest.json icinde version yok" }
Write-Host "Polaris v$version paketleniyor" -ForegroundColor Cyan

# 1) extension derle
if (-not $SkipBuild) {
  & (Join-Path $PSScriptRoot "build-extension.ps1") -Quiet
}
$dist = Join-Path $Root "extension\dist"
if (-not (Test-Path (Join-Path $dist "manifest.json"))) { throw "extension\dist yok; derleme basarisiz mi?" }

# 2) tray (varsa)
$trayBuild = Join-Path $Root "tray\build.ps1"
$trayExe = Join-Path $Root "tray\dist\Polaris.exe"
if ((Test-Path $trayBuild) -and -not $SkipTray) {
  Write-Host "tray\build.ps1 calistiriliyor" -ForegroundColor Cyan
  & powershell -NoProfile -ExecutionPolicy Bypass -File $trayBuild
  if ($LASTEXITCODE -ne 0) { Write-Host "[!] tray derlenemedi; Polaris.exe olmadan devam" -ForegroundColor Yellow }
}

# 3) hazirlik klasoru
$releaseDir = Join-Path $Root "release"
$name = "polaris-v$version"
$stage = Join-Path $releaseDir "_stage\$name"
if (Test-Path (Join-Path $releaseDir "_stage")) { Remove-Item -Recurse -Force (Join-Path $releaseDir "_stage") }
New-Item -ItemType Directory -Force $stage | Out-Null

$excludeDirs = @("__pycache__", ".pytest_cache", "node_modules", "data", "dist", "build", ".venv")
$excludeFiles = @(".env")
$excludeExt = @(".pyc", ".pyo", ".log", ".db", ".spec")

function Copy-Tree([string]$src, [string]$dst) {
  if (-not (Test-Path $src)) { Write-Host "  [!] yok, atlandi: $src" -ForegroundColor Yellow; return }
  New-Item -ItemType Directory -Force $dst | Out-Null
  foreach ($item in Get-ChildItem -LiteralPath $src -Force) {
    if ($item.PSIsContainer) {
      if ($excludeDirs -contains $item.Name) { continue }
      Copy-Tree $item.FullName (Join-Path $dst $item.Name)
    } else {
      if ($excludeFiles -contains $item.Name) { continue }
      if ($item.Name -like ".env.*" -and $item.Name -ne ".env.example") { continue }
      if ($excludeExt -contains $item.Extension.ToLower()) { continue }
      if ($item.Name -like "*.db-*") { continue }
      Copy-Item -LiteralPath $item.FullName (Join-Path $dst $item.Name)
    }
  }
}

Copy-Tree $dist (Join-Path $stage "extension-dist")
foreach ($d in @("backend", "scripts", "shared", "docs", "demo", "tray")) {
  if (Test-Path (Join-Path $Root $d)) { Copy-Tree (Join-Path $Root $d) (Join-Path $stage $d) }
}
foreach ($f in @("README.md", "CHANGELOG.md", "start-polaris.cmd", "pytest.ini")) {
  $p = Join-Path $Root $f
  if (Test-Path $p) { Copy-Item $p (Join-Path $stage $f) } else { Write-Host "  [!] yok: $f" -ForegroundColor Yellow }
}
if (Test-Path $trayExe) {
  Copy-Item $trayExe (Join-Path $stage "Polaris.exe")
  Write-Host "  Polaris.exe (tray) eklendi"
}
# Guvenlik: gizli dosya veya calisma verisi sizmasin
$leak = Get-ChildItem -Recurse -Force $stage | Where-Object {
  ($_.Name -eq ".env") -or ($_.FullName -match '\\backend\\data(\\|$)') -or ($_.Name -match '\.db$')
}
if ($leak) { throw "Pakette olmamasi gereken dosya var: $($leak.FullName -join ', ')" }
if (-not (Test-Path (Join-Path $stage "backend\.env.example"))) { throw "backend\.env.example pakette yok" }

# 4) zip (ileri egik cizgili girdiler: her platformda acilir)
function New-Zip([string]$srcDir, [string]$zipPath, [string]$prefix) {
  if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
  $zip = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
  try {
    $base = (Resolve-Path $srcDir).Path.TrimEnd('\') + '\'
    foreach ($file in Get-ChildItem -Recurse -File -Force $srcDir) {
      $rel = $file.FullName.Substring($base.Length).Replace('\', '/')
      if ($prefix) { $rel = "$prefix/$rel" }
      [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, $rel, [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
  } finally { $zip.Dispose() }
}

$fullZip = Join-Path $releaseDir "$name.zip"
$extZip = Join-Path $releaseDir "polaris-extension-v$version.zip"
New-Zip $stage $fullZip $name
New-Zip $dist $extZip ""

# 5) kurulum sihirbazi (Inno Setup): release\PolarisSetup-<surum>.exe
$setupExe = Join-Path $releaseDir "PolarisSetup-$version.exe"
$outputs = @($fullZip, $extZip)
if ($NoInstaller) {
  Write-Host "  kurulum sihirbazi atlandi (-NoInstaller)" -ForegroundColor Yellow
} elseif (-not (Test-Path (Join-Path $stage "Polaris.exe"))) {
  Write-Host "  [!] Polaris.exe yok (tray derlenemedi); kurulum sihirbazi uretilemedi" -ForegroundColor Yellow
} else {
  & (Join-Path $Root "installer\build-installer.ps1") -StageDir $stage -Version $version -OutDir $releaseDir
  $outputs += $setupExe
}
Remove-Item -Recurse -Force (Join-Path $releaseDir "_stage")

foreach ($z in $outputs) {
  $mb = [math]::Round((Get-Item $z).Length / 1MB, 2)
  Write-Host ("  {0}  ({1} MB)" -f $z, $mb) -ForegroundColor Green
}
Write-Host "Kullanici: PolarisSetup-$version.exe'yi calistirin (ileri duzey: zip'i acin -> $name\start-polaris.cmd)." -ForegroundColor Cyan
