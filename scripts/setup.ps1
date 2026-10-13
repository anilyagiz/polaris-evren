<#
.SYNOPSIS
  Polaris tek komutla kurulum: .venv + backend bagimliliklari + backend/.env + extension bagimliliklari (npm).
  Tekrar tekrar calistirilabilir (idempotent): var olan .venv / .env / modeller korunur.
.PARAMETER WithCua
  Masaustu (CUA) icin ek paketleri de kurar (backend/requirements-cua.txt, agir).
.PARAMETER WithDev
  Test paketlerini de kurar (backend/requirements-dev.txt: pytest vb.).
.PARAMETER DownloadModels
  faster-whisper modelini (backend/.env STT_MODEL, varsayilan small ~460 MB) simdiden indirir;
  boylece ilk sesli istek ~60 sn beklemez. Model zaten onbellekteyse tekrar indirilmez.
.PARAMETER SkipExtension
  Extension bagimliliklarini (npm install) atlar.
.PARAMETER NoTray
  Sistem tepsisi uygulamasinin paketlerini (requirements-tray.txt) kurmaz. Varsayilan: dosya varsa kurulur.
.PARAMETER PythonExe
  Kullanilacak Python (varsayilan: py -3.12, 3.11, 3.13, sonra python). Python 3.14 onerilmez.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -DownloadModels
#>
param(
  [switch]$WithCua,
  [switch]$WithDev,
  [switch]$DownloadModels,
  [switch]$SkipExtension,
  [switch]$NoTray,
  [string]$PythonExe = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  [OK] $t" -ForegroundColor Green }
function Warn($t) { Write-Host "  [!]  $t" -ForegroundColor Yellow }

# 1) Python bul
Step "Python araniyor (3.11-3.13 onerilir)"
$pyCmd = $null
if ($PythonExe) { $pyCmd = @($PythonExe) }
else {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in @("3.12", "3.11", "3.13")) {
      # PS 5.1: EAP=Stop + 2>$null yerel komut stderr'ini hataya cevirir; gecici olarak gevset.
      $ErrorActionPreference = "Continue"
      & py "-$v" -c "import sys" 2>$null | Out-Null
      $code = $LASTEXITCODE
      $ErrorActionPreference = "Stop"
      if ($code -eq 0) { $pyCmd = @("py", "-$v"); break }
    }
  }
  if (-not $pyCmd -and (Get-Command python -ErrorAction SilentlyContinue)) { $pyCmd = @("python") }
}
if (-not $pyCmd) { throw "Python bulunamadi. https://www.python.org/downloads/ adresinden 3.12 kurun." }
$exe = $pyCmd[0]
$pre = @()
if ($pyCmd.Count -gt 1) { $pre = $pyCmd[1..($pyCmd.Count - 1)] }
$ver = & $exe @pre -c "import sys; print('%d.%d' % sys.version_info[:2])"
Ok "Python $ver ($($pyCmd -join ' '))"
if ([version]$ver -ge [version]"3.14") { Warn "Python 3.14 faster-whisper icin guvenilir degil; 3.12 onerilir." }
if ([version]$ver -lt [version]"3.11") { throw "Python 3.11+ gerekli." }

# 2) venv
Step ".venv"
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
  & $exe @pre -m venv .venv
  if ($LASTEXITCODE -ne 0) { throw "venv olusturulamadi" }
  Ok ".venv olusturuldu"
} else { Ok ".venv zaten var" }

# 3) backend bagimliliklari (pip zaten kurulu olanlari atlar)
Step "Backend bagimliliklari (ilk seferde birkac dakika surebilir)"
& $VenvPy -m pip install --upgrade pip --disable-pip-version-check -q
& $VenvPy -m pip install -r backend\requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw "pip install basarisiz" }
Ok "backend/requirements.txt kuruldu"
if ($WithDev) {
  & $VenvPy -m pip install -r backend\requirements-dev.txt --disable-pip-version-check
  if ($LASTEXITCODE -ne 0) { Warn "requirements-dev.txt kurulamadi" } else { Ok "test paketleri kuruldu" }
}
if (-not $NoTray) {
  # Sistem tepsisi uygulamasi (P6). Dosya henuz yoksa sessizce atlanir.
  $trayReq = @("tray\requirements-tray.txt", "tray\requirements.txt", "backend\requirements-tray.txt", "requirements-tray.txt") |
    Where-Object { Test-Path (Join-Path $Root $_) } | Select-Object -First 1
  if ($trayReq) {
    & $VenvPy -m pip install -r $trayReq --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Warn "tepsi paketleri ($trayReq) kurulamadi; start-polaris.cmd konsol moduna duser" }
    else { Ok "tepsi (tray) paketleri kuruldu ($trayReq)" }
  }
}
if ($WithCua) {
  Step "CUA ek paketleri"
  & $VenvPy -m pip install -r backend\requirements-cua.txt --disable-pip-version-check
  if ($LASTEXITCODE -ne 0) { Warn "CUA paketleri kurulamadi; masaustu olmadan devam edebilirsiniz (docs\cua-setup.md)." }
  else { Ok "CUA paketleri kuruldu. Docker Desktop + WSL2 ve .env icinde CUA_ENABLED=true gerekir." }
}

# 4) .env
Step "backend/.env"
$envFile = Join-Path $Root "backend\.env"
if (-not (Test-Path $envFile)) {
  Copy-Item backend\.env.example $envFile
  Ok "backend\.env.example -> backend\.env kopyalandi"
} else { Ok "backend\.env zaten var (degistirilmedi)" }
Write-Host "  Doldurmaniz gereken degerler (backend\.env):" -ForegroundColor White
Write-Host "    DEEPSEEK_API_KEY  -> LLM anahtari (zorunlu)"
Write-Host "    LLM_MODEL         -> model adi (varsayilan deepseek-chat)"
Write-Host "    DEEPSEEK_BASE_URL -> farkli bir OpenAI-uyumlu gecit kullaniyorsaniz"
Write-Host "    BRAVE_API_KEY     -> web.search icin (https://brave.com/search/api/)"
Write-Host "  Anahtarlari asla git'e eklemeyin; .env .gitignore'dadir."

# 5) whisper modeli (istege bagli)
if ($DownloadModels) {
  Step "faster-whisper modeli onceden indiriliyor"
  $model = "small"
  if (Test-Path $envFile) {
    $line = Select-String -Path $envFile -Pattern '^\s*STT_MODEL\s*=' | Select-Object -First 1
    if ($line) {
      $val = ($line.Line -split '=', 2)[1] -replace '\s+#.*$', ''
      $val = $val.Trim().Trim('"').Trim("'")
      if ($val) { $model = $val }
    }
  }
  if ($env:STT_MODEL) { $model = $env:STT_MODEL }
  Write-Host "  Model: $model (small ~460 MB, base ~140 MB; onbellekteyse atlanir)"
  & $VenvPy -c "import sys; from faster_whisper.utils import download_model; p = download_model(sys.argv[1]); print('  model yolu:', p)" $model
  if ($LASTEXITCODE -ne 0) { Warn "Model indirilemedi (internet?). Ilk /stt isteginde tekrar denenecek." }
  else { Ok "whisper modeli hazir ($model)" }
}

# 6) extension (npm; pnpm bu makinede esbuild icin EPERM veriyor)
$extDir = Join-Path $Root "extension"
if ($SkipExtension) {
  Step "Extension atlandi (-SkipExtension)"
} elseif (-not (Test-Path (Join-Path $extDir "package.json"))) {
  Step "Extension kaynagi yok (surum paketi?) - derlenmis extension-dist kullanilir"
} else {
  Step "Extension bagimliliklari (npm install)"
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Warn "npm bulunamadi. Node.js 18+ kurun: https://nodejs.org (npm onunla gelir)"
  } else {
    Push-Location $extDir
    try {
      # Eski pnpm duzeni (node_modules\.pnpm) npm ile karisir: temizle.
      if (Test-Path "node_modules\.pnpm") {
        Warn "pnpm ile kurulmus node_modules bulundu; npm icin temizleniyor"
        Remove-Item -Recurse -Force node_modules
      }
      npm install --no-package-lock --no-audit --no-fund
      if ($LASTEXITCODE -ne 0) { Warn "npm install basarisiz (node_modules silip tekrar deneyin)" }
      else { Ok "extension bagimliliklari kuruldu" }
    } finally { Pop-Location }
  }
}

Step "Bitti"
Write-Host "  En kolay yol: kok klasordeki start-polaris.cmd dosyasina cift tiklayin."
Write-Host "  Elle:"
Write-Host "    1) backend\.env dosyasini doldurun"
Write-Host "    2) .\scripts\run-backend.ps1            (backend'i baslatir)"
Write-Host "    3) .\scripts\build-extension.ps1        (extension'i derler, Brave'e yukleme adimlarini yazar)"
Write-Host "    4) .venv\Scripts\python scripts\doctor.py   (her seyi kontrol eder)"
