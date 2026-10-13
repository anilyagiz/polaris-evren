<#
.SYNOPSIS
  PolarisSetup-<surum>.exe kurulum sihirbazini derler (Inno Setup 6).
  Normalde scripts\package-release.ps1 tarafindan cagrilir (hazir "stage" klasoru ile).

  - Inno Setup derleyicisi (ISCC.exe) yoksa: winget install --id JRSoftware.InnoSetup -e
  - uv (Astral) resmi Windows x64 surumu sabit surum + SHA256 ile indirilir, calistirilarak dogrulanir
    (installer\.cache\ altinda onbelleklenir).
  - polaris.ico + sihirbaz gorselleri extension\public\icons\icon-128.png'den Pillow ile uretilir.
.PARAMETER StageDir
  Kurulacak dosyalarin hazirlandigi klasor (backend, extension-dist, scripts, Polaris.exe ...).
.PARAMETER Version
  Surum (varsayilan: extension\manifest.json).
.PARAMETER OutDir
  Cikti klasoru (varsayilan: release\).
#>
param(
  [Parameter(Mandatory = $true)][string]$StageDir,
  [string]$Version = "",
  [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Root = Split-Path -Parent $Here

# Sabit uv surumu (guncellerken surum + SHA256'yi birlikte degistirin:
#  https://github.com/astral-sh/uv/releases/download/<surum>/uv-x86_64-pc-windows-msvc.zip.sha256)
$UvVersion = "0.12.18"
$UvSha256 = "CAE6A3BC25239F83DFFB467A4B180508D9DA23986C04639EBFA44E43E6A84BFF"

if (-not $Version) {
  $Version = (Get-Content (Join-Path $Root "extension\manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version
}
if (-not $OutDir) { $OutDir = Join-Path $Root "release" }
New-Item -ItemType Directory -Force $OutDir | Out-Null
$StageDir = (Resolve-Path $StageDir).Path
foreach ($must in @("backend\main.py", "backend\.env.example", "extension-dist\manifest.json", "scripts\bootstrap.ps1", "Polaris.exe")) {
  if (-not (Test-Path (Join-Path $StageDir $must))) { throw "Stage eksik: $must ($StageDir)" }
}
if (Test-Path (Join-Path $StageDir "backend\.env")) { throw "Stage icinde backend\.env var - gizli anahtar pakete giremez" }

# 1) ISCC
$iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
  throw "Inno Setup 6 bulunamadi. Kurun: winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements"
}
Write-Host "[setup] ISCC: $iscc"

# 2) uv.exe (onbellek + SHA256 + calistirma dogrulamasi)
$cache = Join-Path $Here ".cache\uv-$UvVersion"
$uvExe = Join-Path $cache "uv.exe"
if (-not (Test-Path $uvExe)) {
  New-Item -ItemType Directory -Force $cache | Out-Null
  $zip = Join-Path $cache "uv.zip"
  $url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip"
  Write-Host "[setup] uv $UvVersion indiriliyor: $url"
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
  $hash = (Get-FileHash $zip -Algorithm SHA256).Hash
  if ($hash -ne $UvSha256) { Remove-Item -Force $zip; throw "uv SHA256 uyusmuyor: $hash" }
  $tmp = Join-Path $cache "x"
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  $found = Get-ChildItem -Recurse -File $tmp -Filter uv.exe | Select-Object -First 1
  if (-not $found) { throw "uv.zip icinde uv.exe yok" }
  Move-Item $found.FullName $uvExe -Force
  Remove-Item -Recurse -Force $tmp, $zip
}
$uvVer = (& $uvExe --version) -join ' '
if ($LASTEXITCODE -ne 0 -or $uvVer -notmatch [regex]::Escape($UvVersion)) { throw "uv.exe calismadi: $uvVer" }
Write-Host "[setup] $uvVer"

# 3) gorseller
$assets = Join-Path $Here "build"
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = (Get-Command python -ErrorAction Stop).Source }
& $py -I (Join-Path $Here "make_assets.py") (Join-Path $Root "extension\public\icons\icon-128.png") $assets
if ($LASTEXITCODE -ne 0) { throw "gorseller uretilemedi (Pillow kurulu mu?)" }

# 4) derle
$iss = Join-Path $Here "polaris.iss"
$isccArgs = @(
  "/Q",
  "/DAppVersion=$Version",
  "/DStageDir=$StageDir",
  "/DUvExe=$uvExe",
  "/DAssetsDir=$assets",
  "/DOutputDir=$OutDir",
  $iss
)
& $iscc @isccArgs
if ($LASTEXITCODE -ne 0) { throw "ISCC basarisiz (kod $LASTEXITCODE)" }
$out = Join-Path $OutDir "PolarisSetup-$Version.exe"
if (-not (Test-Path $out)) { throw "Cikti yok: $out" }
$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host ("[setup] Hazir: {0} ({1} MB)" -f $out, $mb) -ForegroundColor Green
