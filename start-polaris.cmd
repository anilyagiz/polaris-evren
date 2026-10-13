@echo off
rem Polaris - tek tikla baslat. Cift tiklayin; ilk seferde kurulum yapar.
rem   1) .venv yoksa scripts\setup.ps1 (+ whisper modeli)
rem   2) backend\.env yoksa ornekten kopyalar ve Not Defteri'nde acar
rem   3) extension\dist yoksa derler
rem   4) backend'i ayri pencerede baslatir (port: PORT ortam degiskeni > backend\.env PORT > 8000)
rem   5) Brave'e yukleme adimlarini yazar
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "ROOT=%~dp0"
set "PSX=powershell -NoProfile -ExecutionPolicy Bypass"
title Polaris

echo.
echo  ==================== POLARIS ====================
echo.

rem ---------- 1) Kurulum ----------
if exist ".venv\Scripts\python.exe" goto :venv_ok
echo [1/4] Ilk kurulum yapiliyor (Python paketleri, extension, whisper modeli).
echo       Bu islem internet hizina gore 5-15 dakika surebilir...
%PSX% -File "%ROOT%scripts\setup.ps1" -DownloadModels
if errorlevel 1 goto :fail_setup
:venv_ok
echo [1/4] Python ortami hazir (.venv)

rem ---------- 2) backend\.env ----------
if exist "backend\.env" goto :env_ok
copy /y "backend\.env.example" "backend\.env" >nul
echo.
echo  *** backend\.env olusturuldu ***
echo  Simdi Not Defteri acilacak. Lutfen:
echo    - DEEPSEEK_API_KEY= satirina LLM anahtarinizi yazin (zorunlu)
echo    - BRAVE_API_KEY= satirina Brave Search anahtarinizi yazin (web aramasi icin)
echo    - Dosyayi KAYDEDIN ve Not Defteri'ni KAPATIN; kurulum otomatik devam eder.
echo.
start "" /wait notepad "backend\.env"
:env_ok
findstr /r /c:"^DEEPSEEK_API_KEY=buraya" /c:"^DEEPSEEK_API_KEY= *$" "backend\.env" >nul 2>&1
if errorlevel 1 (
  echo [2/4] backend\.env hazir
) else (
  echo [2/4] [UYARI] backend\.env icinde DEEPSEEK_API_KEY bos. LLM cevap veremez.
  echo        Duzenlemek icin: notepad backend\.env  ^(sonra backend penceresini yeniden baslatin^)
)

rem ---------- 3) Extension ----------
set "DIST="
if exist "extension-dist\manifest.json" set "DIST=%ROOT%extension-dist"
if defined DIST goto :dist_ok
if exist "extension\dist\manifest.json" goto :dist_set
echo [3/4] Extension derleniyor (npm)...
%PSX% -File "%ROOT%scripts\build-extension.ps1" -Quiet
if errorlevel 1 goto :fail_build
:dist_set
set "DIST=%ROOT%extension\dist"
:dist_ok
echo [3/4] Extension hazir: %DIST%

rem ---------- 4) Backend ----------
if not defined PORT (
  if exist "backend\.env" (
    for /f "tokens=2 delims==" %%p in ('findstr /b /c:"PORT=" "backend\.env" 2^>nul') do (
      for /f "tokens=1" %%q in ("%%p") do set "PORT=%%q"
    )
  )
)
if not defined PORT set "PORT=8000"

rem cikis: 0 = port bos, 2 = Polaris zaten calisiyor, 1 = baska bir program kullaniyor
%PSX% -Command "$c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if (-not $c) { exit 0 }; try { $r = Invoke-RestMethod -TimeoutSec 3 -Uri 'http://127.0.0.1:%PORT%/health'; if ($r.ok) { exit 2 } } catch {}; $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; Write-Host ('       PID ' + $c.OwningProcess + ' (' + $p.ProcessName + ')'); exit 1"
if errorlevel 2 (
  echo [4/4] Polaris backend zaten calisiyor: http://127.0.0.1:%PORT%
  goto :backend_ready
)
if errorlevel 1 goto :port_busy

rem Tercih: sistem tepsisi uygulamasi (backend'i gizli baslatir). "start-polaris.cmd --console" ile konsol penceresi.
set "MODE=console"
if /i "%~1"=="--console" goto :start_console
if exist "%ROOT%Polaris.exe" (
  set "MODE=tray"
  start "" "%ROOT%Polaris.exe"
  goto :wait_health
)
if exist "%ROOT%tray\dist\Polaris.exe" (
  set "MODE=tray"
  start "" "%ROOT%tray\dist\Polaris.exe"
  goto :wait_health
)
if not exist "%ROOT%tray\__main__.py" goto :start_console
if not exist ".venv\Scripts\pythonw.exe" goto :start_console
rem Tepsi kodu ve bagimliliklari (requirements-tray) yuklenebiliyor mu?
".venv\Scripts\python.exe" -c "import tray.app" >nul 2>&1
if not errorlevel 1 (
  set "MODE=tray"
  start "" ".venv\Scripts\pythonw.exe" -m tray
  goto :wait_health
)
:start_console
echo [4/4] Backend ayri pencerede baslatiliyor (port %PORT%)...
start "Polaris Backend :%PORT%" %PSX% -NoExit -File "%ROOT%scripts\run-backend.ps1" -NoReload -Port %PORT%
:wait_health
if "%MODE%"=="tray" echo [4/4] Polaris tepsi uygulamasi baslatildi (saatin yanindaki Polaris yildizi); backend bekleniyor...
%PSX% -Command "for ($i = 0; $i -lt 60; $i++) { try { $r = Invoke-RestMethod -TimeoutSec 2 -Uri 'http://127.0.0.1:%PORT%/health'; if ($r.ok) { exit 0 } } catch {}; Start-Sleep -Seconds 1 }; exit 1"
if errorlevel 1 if "%MODE%"=="tray" (
  echo [UYARI] Tepsi uygulamasi backend'i baslatamadi; konsol moduna geciliyor...
  set "MODE=console"
  goto :start_console
)
if errorlevel 1 (
  echo [UYARI] Backend 60 sn icinde /health yanit vermedi.
  echo         Konsol modunda hatayi gormek icin:  start-polaris.cmd --console
  echo         Teshis: .venv\Scripts\python scripts\doctor.py
) else (
  echo [4/4] Backend hazir: http://127.0.0.1:%PORT%/health
)

:backend_ready
%PSX% -Command "try { Set-Clipboard -Value $env:DIST } catch {}" >nul 2>&1
echo.
echo  ================ BRAVE'E YUKLEME ================
echo   1) Brave adres cubuguna yazin:  brave://extensions
echo   2) Sag ustte "Gelistirici modu"nu acin
echo   3) "Paketlenmemis uzantiyi yukle" -^> su klasoru secin (panoya kopyalandi):
echo        %DIST%
echo   4) Polaris simgesine tiklayin; yan panel acilir
echo   5) Ilk bas-konus'ta mikrofon iznini verin
echo   6) Panel Ayarlar -^> Backend adresi: http://localhost:%PORT%
echo  =================================================
echo   Uzanti zaten yukluyse brave://extensions'ta "yenile" yeterli.
if "%MODE%"=="tray" (
  echo   Backend'i durdurmak icin: saatin yanindaki Polaris yildizi -^> sag tik -^> Cikis.
) else (
  echo   Backend'i durdurmak icin "Polaris Backend" penceresini kapatin.
)
echo.
pause
exit /b 0

:port_busy
echo.
echo [HATA] Port %PORT% baska bir program tarafindan kullaniliyor (yukaridaki PID).
echo   Cozum A: O programi kapatin (veya: taskkill /PID ^<pid^> /F) ve tekrar cift tiklayin.
echo   Cozum B: Baska bir port secin. backend\.env dosyasina su satiri ekleyin:
echo              PORT=8010
echo            ya da bu pencerede:  set PORT=8010 ^&^& start-polaris.cmd
echo            Sonra panel Ayarlar -^> Backend adresi: http://localhost:8010
echo.
pause
exit /b 1

:fail_setup
echo.
echo [HATA] Kurulum basarisiz. Yukaridaki mesaji okuyun.
echo   - Python 3.12 kurulu mu?  https://www.python.org/downloads/  ("Add to PATH" isaretli)
echo   - Elle deneyin: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
echo   - Yarim kalan .venv klasorunu silip tekrar cift tiklayin.
pause
exit /b 1

:fail_build
echo.
echo [HATA] Extension derlenemedi.
echo   - Node.js 18+ kurulu mu?  https://nodejs.org
echo   - extension\node_modules klasorunu silip tekrar deneyin (pnpm DEGIL, npm kullanilir).
pause
exit /b 1
