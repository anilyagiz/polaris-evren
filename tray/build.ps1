<#
.SYNOPSIS
  Polaris tepsi uygulamasini tek dosyalik Polaris.exe olarak derler (PyInstaller).

.DESCRIPTION
  Yalnizca tray/ kodu + pystray + Pillow + pywebview (WebView2) paketlenir; backend PAKETLENMEZ.
  Exe calisirken kendi klasorunden yukari dogru backend\ klasorunu arar ve
  o kokteki .venv\Scripts\pythonw.exe ile backend'i baslatir. Bu yuzden
  Polaris.exe'yi repo kokune veya surum (release) kokune kopyalayin.

  Cikti : tray\dist\Polaris.exe   (+ tray\dist\polaris.ico)
  Ayrica: tray\Polaris.vbs (PyInstaller olmadan konsolsuz baslatma yedegi)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tray\build.ps1
  powershell -ExecutionPolicy Bypass -File tray\build.ps1 -Smoke
#>
param(
    [switch]$Smoke,          # derleme sonrasi: Polaris.exe --smoke
    [switch]$SkipInstall     # pip install adimini atla
)
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Py   = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { throw ".venv bulunamadi ($Py). Once scripts\setup.ps1 calistirin." }

$Dist = Join-Path $PSScriptRoot 'dist'
$Work = Join-Path $env:TEMP 'polaris-tray-build'
$Ico  = Join-Path $Dist 'polaris.ico'
New-Item -ItemType Directory -Force $Dist | Out-Null

if (-not $SkipInstall) {
    Write-Host '[tray] pystray, Pillow, pywebview, pyinstaller kuruluyor...'
    & $Py -m pip install --disable-pip-version-check -q -r (Join-Path $Root 'backend\requirements-tray.txt') pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'pip install basarisiz' }
}

Write-Host '[tray] ikon olusturuluyor...'
Push-Location $Root
try {
    & $Py -c "from pathlib import Path; from tray.icons import write_ico; write_ico(Path(r'$Ico'))"
    if ($LASTEXITCODE -ne 0) { throw 'ikon olusturulamadi' }

    # Surum bilgisi kaynagi (Ozellikler -> Ayrintilar, SmartScreen): surum extension\manifest.json'dan
    $ver = (Get-Content (Join-Path $Root 'extension\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json).version
    $nums = @(($ver -split '[^0-9]+' | Where-Object { $_ -ne '' } | Select-Object -First 4) | ForEach-Object { [int]$_ })
    while ($nums.Count -lt 4) { $nums += 0 }
    $tuple = ($nums -join ', ')
    $VerFile = Join-Path $Work 'version_info.txt'
    New-Item -ItemType Directory -Force $Work | Out-Null
    $verText = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($tuple), prodvers=($tuple), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041F04B0', [
      StringStruct('CompanyName', 'EVREN Asistan'),
      StringStruct('FileDescription', 'Polaris'),
      StringStruct('FileVersion', '$ver'),
      StringStruct('InternalName', 'Polaris'),
      StringStruct('LegalCopyright', '(c) EVREN Asistan'),
      StringStruct('OriginalFilename', 'Polaris.exe'),
      StringStruct('ProductName', 'Polaris'),
      StringStruct('ProductVersion', '$ver')])]),
    VarFileInfo([VarStruct('Translation', [1055, 1200])])
  ]
)
"@
    [System.IO.File]::WriteAllText($VerFile, $verText, (New-Object System.Text.UTF8Encoding($false)))

    Write-Host '[tray] PyInstaller...'
    $excludes = @('numpy','scipy','pandas','torch','onnxruntime','ctranslate2','faster_whisper','av',
                  'fastapi','uvicorn','starlette','pydantic','openai','httpx','backend','tkinter',
                  'pytest','cv2','matplotlib','IPython')
    $argsList = @('-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
                  '--name','Polaris','--icon',$Ico,'--version-file',$VerFile,
                  '--distpath',$Dist,'--workpath',$Work,'--specpath',$Work,
                  '--paths',$Root,'--hidden-import','pystray._win32',
                  # Masaustu maskotu (tray\mascot.py) app.py'de tembel import edilir.
                  '--hidden-import','tray.mascot','--hidden-import','tray.desktop_window',
                  # Polaris penceresi: pywebview WinForms + WebView2 (edgechromium) arka ucu;
                  # pythonnet/clr_loader .NET koprusu (hook'lari DLL'leri toplar).
                  '--hidden-import','webview.platforms.winforms',
                  '--hidden-import','webview.platforms.edgechromium',
                  '--hidden-import','clr','--hidden-import','clr_loader','--hidden-import','pythonnet',
                  '--collect-data','webview','--collect-binaries','webview',
                  '--exclude-module','webview.platforms.qt','--exclude-module','webview.platforms.gtk',
                  '--exclude-module','webview.platforms.cocoa','--exclude-module','webview.platforms.cef',
                  '--exclude-module','webview.platforms.android')
    foreach ($m in $excludes) { $argsList += @('--exclude-module', $m) }
    $argsList += (Join-Path $PSScriptRoot 'launch.pyw')
    & $Py @argsList
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller basarisiz' }
} finally { Pop-Location }

$Exe = Join-Path $Dist 'Polaris.exe'
$size = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host "[tray] Hazir: $Exe ($size MB)"
Write-Host '[tray] Surum paketine kopyalayin: <kok>\Polaris.exe (backend\ ve .venv\ ile ayni klasor).'

if ($Smoke) {
    Write-Host '[tray] Smoke: Polaris.exe --smoke (cikti backend\data\logs\tray.log icinde)'
    $p = Start-Process -FilePath $Exe -ArgumentList '--smoke' -PassThru -Wait
    if ($p.ExitCode -ne 0) { throw "Smoke basarisiz (cikis kodu $($p.ExitCode))" }
    Write-Host '[tray] Smoke OK'
}
