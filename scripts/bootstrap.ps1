<#
.SYNOPSIS
  Polaris çalışma ortamını kurar/onarır (Kurulum sihirbazı ve "Polaris'i onar" kısayolu bunu çalıştırır).
  Sistemde Python GEREKMEZ: paketle gelen uv.exe ile Python 3.12 kök klasörün içine indirilir.

  Adımlar (hepsi idempotent; tekrar çalıştırmak güvenlidir):
    1) uv python install 3.12        -> <kök>\runtime\python  (sistem/registry/PATH'e dokunmaz)
    2) .venv (uv-managed 3.12 ile)    -> <kök>\.venv
    3) uv pip install -r backend\requirements.txt (+ tepsi paketleri)
    4) backend\.env yoksa .env.example'dan kopyalanır (var olana dokunulmaz)
    5) (-DownloadModels) faster-whisper modeli indirilir; TTS modeli varsa ısıtılır

  Kurulum sihirbazı için makine-okunur satırlar basar:  "@@STEP <ad>", "@@WARN <kod>", "@@ERROR <kod>", "@@DONE".
  Çıkış kodları: 0 tamam, 2 ağ/indirme hatası (internet), 3 diğer hata.
.PARAMETER DownloadModels
  Ses tanıma modelini (STT_MODEL, varsayılan small ~460 MB) şimdi indirir.
.PARAMETER Pause
  Bitince pencereyi açık tutar (Başlat menüsündeki "Polaris'i onar" kısayolu).
.PARAMETER Root
  Polaris kök klasörü (varsayılan: bu betiğin bir üstü).
.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -DownloadModels -Pause
#>
param(
  [switch]$DownloadModels,
  [switch]$Pause,
  [string]$Root = ""
)
$ErrorActionPreference = "Stop"
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path $Root).Path.TrimEnd('\')
Set-Location $Root

$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$LogFile = Join-Path $LogDir "install.log"   # sihirbaz "Ayrıntıları göster" bunu açar
$Utf8 = New-Object System.Text.UTF8Encoding($false)
# Yerel komut çıktıları (uv, python) UTF-8; sihirbazın günlüğü de UTF-8 okur.
try { [Console]::OutputEncoding = $Utf8 } catch {}
$OutputEncoding = $Utf8

function Log([string]$t) {
  Write-Host $t
  try { [System.IO.File]::AppendAllText($LogFile, ("[{0:HH:mm:ss}] {1}`r`n" -f (Get-Date), $t), $Utf8) } catch {}
}
function Step([string]$id, [string]$t) { Log "@@STEP $id"; Log "==> $t" }
function Ok([string]$t) { Log "  [OK] $t" }
function Warn([string]$code, [string]$t) { Log "@@WARN $code"; Log "  [!] $t" }

class BootstrapError : System.Exception {
  [int]$Code; [string]$Tag
  BootstrapError([int]$code, [string]$tag, [string]$msg) : base($msg) { $this.Code = $code; $this.Tag = $tag }
}

# Ağ hatası mı? (uv/pip/huggingface mesajlarından)
$NetPattern = 'error sending request|dns error|failed to (download|fetch)|timed? ?out|connection (refused|reset|closed)|' +
              'tcp connect|network|could not connect|no such host|unreachable|getaddrinfo|ssl|certificate|' +
              'HTTP status (5\d\d|429)|Max retries exceeded|ConnectionError|LocalEntryNotFoundError|offline'

# Yerel komutu çalıştırır, çıktıyı loglar; başarısızsa $Retries kez (artan bekleme ile) tekrar dener.
function Invoke-Native([string]$exe, [string[]]$argv, [int]$Retries = 1, [string]$Tag = "cmd") {
  for ($attempt = 1; $attempt -le $Retries; $attempt++) {
    $script:lastOut = New-Object System.Collections.Generic.List[string]
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
      & $exe @argv 2>&1 | ForEach-Object {
        $line = "$_"
        if ($line.Trim()) { $script:lastOut.Add($line); Log "    $line" }
      }
      $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
    if ($code -eq 0) { return }
    $text = ($script:lastOut -join "`n")
    $isNet = $text -match $NetPattern
    if ($attempt -lt $Retries -and $isNet) {   # yalnızca ağ hatalarında tekrar dene
      $wait = 5 * $attempt
      Log "  [!] Başarısız (kod $code). $wait sn sonra tekrar deneniyor ($($attempt + 1)/$Retries)..."
      Start-Sleep -Seconds $wait
      continue
    }
    if ($isNet) { throw [BootstrapError]::new(2, "net", "${Tag}: ağ hatası (kod $code)") }
    throw [BootstrapError]::new(3, $Tag, "$Tag başarısız (kod $code)")
  }
}

function Stop-PolarisProcesses {
  # Bu köke ait tepsi (Polaris.exe) ve backend (.venv\Scripts\python*.exe) süreçlerini kapatır;
  # aksi halde kilitli .pyd dosyaları paket kurulumunu bozar.
  $prefix = $Root + '\'
  $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase) -and
    $_.ProcessId -ne $PID
  }
  foreach ($p in $procs) {
    Log "  çalışan süreç kapatılıyor: $($p.Name) (PID $($p.ProcessId))"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
  if ($procs) { Start-Sleep -Seconds 1 }
}

$exitCode = 0
try {
  Log "Polaris bootstrap başladı: $Root"

  # --- uv ---
  $uv = @(
    (Join-Path $Root "tools\uv\uv.exe"),
    (Join-Path $Root "tools\uv.exe")
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $uv) {
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { $uv = $cmd.Source }
  }
  if (-not $uv) { throw [BootstrapError]::new(3, "uv", "uv.exe bulunamadı (tools\uv\uv.exe). Kurulumu yeniden çalıştırın.") }

  # Her şey kök klasörde kalsın: kaldırınca iz bırakmasın, sistem Python'una karışmasın.
  $Runtime = Join-Path $Root "runtime"
  New-Item -ItemType Directory -Force (Join-Path $Runtime "python") | Out-Null
  $RuntimeUv = $Runtime
  if ($Runtime -cmatch '[^\x00-\x7F]') {
    # uv, ASCII dışı yollarda (ör. C:\Users\anıl) Python bağlantı klasörünü doğrulayamıyor:
    # mümkünse aynı klasörün 8.3 kısa adını (ASCII) kullan. (-cmatch: tr-TR'de 'I' -> 'ı' tuzağı)
    try {
      $short = (New-Object -ComObject Scripting.FileSystemObject).GetFolder($Runtime).ShortPath
      if ($short -and $short -cnotmatch '[^\x00-\x7F]') { $RuntimeUv = $short; Log "  kısa yol kullanılıyor: $short" }
    } catch {}
  }
  $env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeUv "python"
  $env:UV_CACHE_DIR = Join-Path $RuntimeUv "uv-cache"
  $env:UV_PYTHON_PREFERENCE = "only-managed"
  $env:UV_HTTP_TIMEOUT = "120"
  $env:UV_NO_PROGRESS = "1"
  $env:UV_NO_CONFIG = "1"
  $env:VIRTUAL_ENV = $null
  $env:PYTHONUTF8 = "1"
  $env:PYTHONIOENCODING = "utf-8"
  Remove-Item Env:PYTHONHOME, Env:PYTHONPATH -ErrorAction SilentlyContinue
  Ok ("uv: " + ((& $uv --version) -join ' '))

  Stop-PolarisProcesses

  # --- 1) Python 3.12 ---
  Step "python" "Python 3.12 hazırlanıyor (uv, sistem Python'u gerekmez)"
  $installErr = $null
  try { Invoke-Native $uv @("python", "install", "3.12", "--no-bin", "--no-registry") -Retries 3 -Tag "python" }
  catch { $installErr = $_.Exception }
  # uv kurduğu Python'u bulur; "3.12" kısayol bağlantısı (junction) oluşturulamadıysa
  # (ASCII dışı yol / güvenlik ilkesi) doğrudan indirilen klasördeki python.exe kullanılır.
  $pyFound = $null
  if (-not $installErr) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $pyFound = (& $uv python find 3.12 --managed-python 2>$null | Select-Object -First 1)
    $ErrorActionPreference = $prev
  }
  if (-not $pyFound -or -not (Test-Path $pyFound)) {
    $pyFound = $null
    $cand = Get-ChildItem -Directory (Join-Path $Runtime "python") -Filter "cpython-3.12.*-windows-x86_64-none" -ErrorAction SilentlyContinue |
      Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
      Sort-Object { [version](($_.Name -split '-')[1]) } -Descending |
      ForEach-Object { Join-Path $_.FullName "python.exe" } |
      Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($cand) {
      $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
      & $cand -c "import ssl, sqlite3, venv" 2>$null
      $ok = $LASTEXITCODE -eq 0
      $ErrorActionPreference = $prev
      if ($ok) {
        if ($installErr) { Warn "pylink" "uv '3.12' bağlantı klasörünü oluşturamadı; indirilen Python doğrudan kullanılıyor." }
        $pyFound = $cand
      }
    }
  }
  if (-not $pyFound) {
    if ($installErr) { throw $installErr }
    throw [BootstrapError]::new(3, "python", "uv Python 3.12 bulunamadı")
  }
  Ok "Python 3.12: $pyFound"

  # --- 2) .venv ---
  Step "venv" "Sanal ortam (.venv)"
  $Venv = Join-Path $Root ".venv"
  $VenvPy = Join-Path $Venv "Scripts\python.exe"
  $needVenv = $true
  if (Test-Path $VenvPy) {
    # .venv kök klasördeki uv Python 3.12'yi mi kullanıyor? (kısa/uzun yol farkını Python çözer)
    $chk = "import os, sys; n = lambda p: os.path.normcase(os.path.realpath(p)); " +
           "print('OK' if sys.version_info[:2] == (3, 12) and n(sys.base_prefix).startswith(n(sys.argv[1])) else 'NO')"
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $ans = (& $VenvPy -c $chk (Join-Path $Runtime "python") 2>$null | Select-Object -Last 1)
    $ErrorActionPreference = $prev
    if ($ans -eq "OK") {
      $needVenv = $false
      Ok ".venv zaten var ve uv Python 3.12 kullanıyor (yalnızca paketler eşitlenecek)"
    } else {
      Log "  .venv farklı/bozuk bir Python ile kurulmuş; yeniden oluşturuluyor"
      Remove-Item -Recurse -Force $Venv
    }
  }
  if ($needVenv) {
    Invoke-Native $uv @("venv", $Venv, "--python", $pyFound, "--seed") -Tag "venv"
    Ok ".venv oluşturuldu"
  }

  # --- 3) paketler ---
  Step "deps" "Python paketleri kuruluyor (ilk seferde birkaç dakika sürebilir)"
  $req = @("-r", (Join-Path $Root "backend\requirements.txt"))
  $trayReq = Join-Path $Root "backend\requirements-tray.txt"
  if (Test-Path $trayReq) { $req += @("-r", $trayReq) }
  Invoke-Native $uv (@("pip", "install", "--python", $VenvPy) + $req) -Retries 3 -Tag "deps"
  Invoke-Native $VenvPy @("-c", "import fastapi, uvicorn, openai, faster_whisper, pystray; print('import OK')") -Tag "deps"
  Ok "paketler kuruldu"

  # --- 4) backend\.env ---
  Step "env" "Ayar dosyası (backend\.env)"
  $envFile = Join-Path $Root "backend\.env"
  if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $Root "backend\.env.example") $envFile
    Ok "backend\.env.example -> backend\.env (API anahtarını panelden girin: Ayarlar -> Yapay zeka)"
  } else { Ok "backend\.env zaten var (değiştirilmedi)" }

  # --- 5) modeller (isteğe bağlı) ---
  if ($DownloadModels) {
    Step "model" "Ses tanıma modeli indiriliyor (~460 MB)"
    $model = "small"
    $line = Select-String -Path $envFile -Pattern '^\s*STT_MODEL\s*=' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($line) {
      $val = (($line.Line -split '=', 2)[1] -replace '\s+#.*$', '').Trim().Trim('"').Trim("'")
      if ($val) { $model = $val }
    }
    Log "  model: $model"
    $env:HF_HUB_DISABLE_PROGRESS_BARS = "1"
    try {
      Invoke-Native $VenvPy @("-c", "import sys; from faster_whisper.utils import download_model; print('model:', download_model(sys.argv[1]))", $model) -Retries 3 -Tag "model"
      Ok "ses modeli hazır ($model)"
    } catch {
      # Model şart değil: ilk sesli istekte yeniden denenir.
      Warn "model" "Ses modeli indirilemedi ($($_.Exception.Message)). İlk sesli istekte tekrar denenecek."
    }

    Step "tts" "Ses sentezi (TTS) hazırlanıyor"
    $ttsPy = @'
import asyncio, inspect, sys
sys.path.insert(0, sys.argv[1])
try:
    import backend.voice.tts as t
except Exception as e:
    print("tts import edilemedi:", e); sys.exit(0)
for name in ("prefetch_models", "download_models", "prefetch", "warmup_sync"):
    fn = getattr(t, name, None)
    if callable(fn):
        r = fn()
        if inspect.isawaitable(r):
            asyncio.run(r)
        print("tts:", name, "tamam"); break
else:
    # EMA Lightning (yerel TTS) kuruluysa modeli simdi indir/yukle (~34 MB); yoksa edge-tts (cevrimici).
    ema_ok = getattr(t, "ema_importable", lambda: False)()
    get_ema = getattr(t, "_get_ema", None)
    if ema_ok and callable(get_ema):
        get_ema(); print("tts: EMA modeli hazir")
    else:
        print("tts: indirilecek yerel model yok (edge-tts cevrimici)")
'@
    $ttsFile = Join-Path $Runtime "tts_warm.py"
    [System.IO.File]::WriteAllText($ttsFile, $ttsPy, $Utf8)
    try { Invoke-Native $VenvPy @($ttsFile, $Root) -Tag "tts"; Ok "TTS hazır" }
    catch { Warn "tts" "TTS ısıtılamadı ($($_.Exception.Message)); Polaris yine çalışır." }
  }

  Log "@@DONE"
  Log "Polaris hazır. Masaüstündeki 'Polaris' kısayolu ile başlatın."
}
catch {
  $e = $_.Exception
  if ($e -is [BootstrapError]) { $exitCode = $e.Code; $tag = $e.Tag } else { $exitCode = 3; $tag = "other" }
  Log "@@ERROR $tag"
  Log "[HATA] $($e.Message)"
  if ($exitCode -eq 2) {
    Log "İnternet bağlantısı kurulamadı ya da sunucu meşgul. Bağlantınızı kontrol edip"
    Log "Başlat menüsü -> Polaris -> 'Polaris'i onar' ile tekrar deneyin (kaldığı yerden devam eder)."
  } else {
    Log "Ayrıntılar: $LogFile"
    Log "Tekrar denemek için: Başlat menüsü -> Polaris -> 'Polaris'i onar'."
  }
}
finally {
  if ($Pause) {
    Write-Host ""
    if ($exitCode -eq 0) { Write-Host "Tamam. Kapatmak için Enter'a basın." -ForegroundColor Green }
    else { Write-Host "Onarım tamamlanamadı. Kapatmak için Enter'a basın." -ForegroundColor Yellow }
    [void](Read-Host)
  }
}
exit $exitCode
