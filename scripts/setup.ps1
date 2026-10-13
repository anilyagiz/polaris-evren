<#
.SYNOPSIS
  Polaris tek komutla kurulum: .venv + backend bagimliliklari + backend/.env + extension bagimliliklari.
.PARAMETER WithCua
  Masaustu (CUA) icin ek paketleri de kurar (backend/requirements-cua.txt, agir).
.PARAMETER PythonExe
  Kullanilacak Python (varsayilan: py -3.12, 3.11, 3.13, sonra python). Python 3.14 onerilmez.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -WithCua
#>
param(
  [switch]$WithCua,
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
      & py "-$v" -c "import sys" 2>$null
      if ($LASTEXITCODE -eq 0) { $pyCmd = @("py", "-$v"); break }
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
Step ".venv olusturuluyor"
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
  & $exe @pre -m venv .venv
  if ($LASTEXITCODE -ne 0) { throw "venv olusturulamadi" }
  Ok ".venv olusturuldu"
} else { Ok ".venv zaten var" }

# 3) backend bagimliliklari
Step "Backend bagimliliklari kuruluyor (ilk seferde birkac dakika surebilir)"
& $VenvPy -m pip install --upgrade pip | Out-Null
& $VenvPy -m pip install -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install basarisiz" }
Ok "backend/requirements.txt kuruldu"
if ($WithCua) {
  Step "CUA ek paketleri kuruluyor"
  & $VenvPy -m pip install -r backend\requirements-cua.txt
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

# 5) extension
Step "Extension bagimliliklari (pnpm install)"
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
  Warn "pnpm bulunamadi. Kurulum: npm install -g pnpm   (veya: corepack enable)"
} else {
  Push-Location extension
  try {
    pnpm install
    if ($LASTEXITCODE -ne 0) { Warn "pnpm install basarisiz" } else { Ok "extension bagimliliklari kuruldu" }
  } finally { Pop-Location }
}

Step "Bitti"
Write-Host "  Sonraki adimlar:"
Write-Host "    1) backend\.env dosyasini doldurun"
Write-Host "    2) .\scripts\run-backend.ps1            (backend'i baslatir)"
Write-Host "    3) .\scripts\build-extension.ps1        (extension'i derler, Brave'e yukleme adimlarini yazar)"
Write-Host "    4) .venv\Scripts\python scripts\doctor.py   (her seyi kontrol eder)"
