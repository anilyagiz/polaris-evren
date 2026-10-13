; Polaris kurulum sihirbazi (Inno Setup 6.4+).
; Derleme: installer\build-installer.ps1 (scripts\package-release.ps1 bunu cagirir).
; Elle:    ISCC.exe /DAppVersion=0.1.1 /DStageDir=<stage> /DUvExe=<uv.exe> /DAssetsDir=<build> /DOutputDir=<release> installer\polaris.iss
;
; Sessiz kurulum (test / BT):  PolarisSetup-<ver>.exe /VERYSILENT /SUPPRESSMSGBOXES /DIR="C:\yol" /TASKS="desktopicon,voicemodel"
;   Gorevler: desktopicon (varsayilan acik), voicemodel (varsayilan acik), autostart (varsayilan kapali)
;   /TASKS="" ile hicbiri;  /LOG="kurulum.log" ile ayrintili gunluk.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #error StageDir tanimli degil (build-installer.ps1 kullanin)
#endif
#ifndef UvExe
  #error UvExe tanimli degil
#endif
#ifndef AssetsDir
  #error AssetsDir tanimli degil (polaris.ico, wizard-*.bmp)
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif

#define AppName "Polaris"
#define AppPublisher "EVREN Asistan"
#define AppExe "Polaris.exe"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"
; tray\autostart.py VALUE_NAME ile ayni olmali (tepsi menusundeki "Windows acilisinda baslat" ayni degeri kullanir)
#define RunValue "Polaris"

[Setup]
AppId={{2CD58903-422C-437E-9A4F-650E698F0B0C}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/anilyagiz/polaris-evren
AppSupportURL=https://github.com/anilyagiz/polaris-evren/issues
AppUpdatesURL=https://github.com/anilyagiz/polaris-evren/releases
AppContact={#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoCopyright=(c) {#AppPublisher}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoTextVersion={#AppVersion}
VersionInfoDescription={#AppName} Kurulum
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Polaris
DisableDirPage=auto
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
WizardStyle=modern dynamic
WizardSizePercent=110
SetupIconFile={#AssetsDir}\polaris.ico
WizardImageFile={#AssetsDir}\wizard-large.bmp,{#AssetsDir}\wizard-large-2x.bmp
WizardSmallImageFile={#AssetsDir}\wizard-small.bmp,{#AssetsDir}\wizard-small-2x.bmp
UninstallDisplayIcon={app}\polaris.ico
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=PolarisSetup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=no
RestartApplications=no
SetupLogging=yes
ChangesEnvironment=no

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[CustomMessages]
turkish.TaskDesktop=Masaüstüne "Polaris" kısayolu ekle
turkish.TaskVoice=Ses modelini şimdi indir (~460 MB; ilk sesli komut beklemez)
turkish.TaskAutostart=Windows açılışında Polaris'i başlat
turkish.GroupShortcuts=Kısayollar:
turkish.GroupOptions=Seçenekler:
turkish.RunPolaris=Polaris'i başlat
turkish.RunGuide=Brave kurulum rehberini aç
turkish.IconRepair=Polaris'i onar
turkish.IconGuide=Brave'e eklentiyi yükle (rehber)
turkish.IconUninstall=Polaris'i kaldır

[Tasks]
Name: "desktopicon"; Description: "{cm:TaskDesktop}"; GroupDescription: "{cm:GroupShortcuts}"
Name: "voicemodel"; Description: "{cm:TaskVoice}"; GroupDescription: "{cm:GroupOptions}"
Name: "autostart"; Description: "{cm:TaskAutostart}"; GroupDescription: "{cm:GroupOptions}"; Flags: unchecked

[InstallDelete]
; Tamamen yenilenen klasorler (eski surumden kalan dosyalar karismasin). backend\ (data, .env) korunur.
Type: filesandordirs; Name: "{app}\extension-dist"
Type: filesandordirs; Name: "{app}\shared"
Type: filesandordirs; Name: "{app}\tray"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\tools\uv"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#UvExe}"; DestDir: "{app}\tools\uv"; Flags: ignoreversion
Source: "{#AssetsDir}\polaris.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "guide.html"; DestDir: "{app}\guide"; Flags: ignoreversion

[Dirs]
Name: "{app}\logs"

[Icons]
Name: "{autoprograms}\{#AppName}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\polaris.ico"; Comment: "Polaris sesli asistanı başlat"
Name: "{autoprograms}\{#AppName}\{cm:IconRepair}"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\bootstrap.ps1"" -Pause"; WorkingDir: "{app}"; IconFilename: "{app}\polaris.ico"; Comment: "Python ortamını ve paketleri yeniden kurar/onarır"
Name: "{autoprograms}\{#AppName}\{cm:IconGuide}"; Filename: "{code:BraveExe}"; Parameters: """{app}\guide\guide.html"""; IconFilename: "{app}\polaris.ico"; Check: HasBrave
Name: "{autoprograms}\{#AppName}\{cm:IconGuide}"; Filename: "{app}\guide\guide.html"; IconFilename: "{app}\polaris.ico"; Check: not HasBrave
Name: "{autoprograms}\{#AppName}\{cm:IconUninstall}"; Filename: "{uninstallexe}"; IconFilename: "{app}\polaris.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; IconFilename: "{app}\polaris.ico"; Tasks: desktopicon; Comment: "Polaris sesli asistanı başlat"

[Registry]
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "{#RunValue}"; ValueData: """{app}\{#AppExe}"" --hidden"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Description: "{cm:RunPolaris}"; Flags: nowait postinstall skipifsilent; Check: BootstrapOk
Filename: "{code:BraveExe}"; Parameters: """{app}\guide\guide.html"""; Description: "{cm:RunGuide}"; Flags: nowait postinstall skipifsilent; Check: HasBrave
Filename: "{app}\guide\guide.html"; Description: "{cm:RunGuide}"; Flags: shellexec nowait postinstall skipifsilent; Check: not HasBrave

[Code]
var
  ProgressPage: TOutputProgressWizardPage;
  BootstrapResult: Integer;
  BootstrapErrorTag: String;
  StepIndex, StepTotal: Integer;
  KeepUserData: Boolean;
  BravePage: TInputOptionWizardPage;
  BraveNote: String;
  InstallDone: Boolean;

{ ---------------- yardimcilar ---------------- }

function BraveExe(Param: String): String;
var
  Candidates: array of String;
  I: Integer;
begin
  Result := '';
  SetArrayLength(Candidates, 3);
  Candidates[0] := ExpandConstant('{localappdata}\BraveSoftware\Brave-Browser\Application\brave.exe');
  Candidates[1] := ExpandConstant('{commonpf64}\BraveSoftware\Brave-Browser\Application\brave.exe');
  Candidates[2] := ExpandConstant('{commonpf32}\BraveSoftware\Brave-Browser\Application\brave.exe');
  for I := 0 to GetArrayLength(Candidates) - 1 do
    if FileExists(Candidates[I]) then
    begin
      Result := Candidates[I];
      Exit;
    end;
end;

function HasBrave: Boolean;
begin
  Result := BraveExe('') <> '';
end;

function BootstrapOk: Boolean;
begin
  Result := BootstrapResult = 0;
end;

function PowerShellExe: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

{ Kurulum klasorunden calisan Polaris sureclerini (tepsi + backend pythonw) kapatir. }
procedure StopPolarisProcesses(const Dir: String);
var
  Cmd: String;
  Code: Integer;
begin
  if (Dir = '') or not DirExists(Dir) then Exit;
  Cmd := '-NoProfile -ExecutionPolicy Bypass -Command "$d = [IO.Path]::GetFullPath(''' + Dir + ''').TrimEnd(''\'') + ''\''; ' +
         'Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) } | ' +
         'ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"';
  Exec(PowerShellExe, Cmd, '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(500);
end;

{ JS dizesi icin kacis: ASCII disi karakterler \uXXXX, ters bolu ve tirnak kacisli. }
function JsEscape(const S: String): String;
var
  I, C: Integer;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := Ord(S[I]);
    if S[I] = '\' then Result := Result + '\\'
    else if S[I] = '"' then Result := Result + '\"'
    else if (C < 32) or (C > 126) then Result := Result + '\u' + Format('%.4x', [C])
    else Result := Result + S[I];
  end;
end;

{ Rehberdeki "Klasor yolunu kopyala" dugmesi icin gercek yolu yazar (saf ASCII: kodlama sorunu olmaz). }
procedure FillGuide;
begin
  SaveStringToFile(ExpandConstant('{app}\guide\polaris-path.js'),
    'window.POLARIS_EXT_PATH = "' + JsEscape(ExpandConstant('{app}\extension-dist')) + '";' + #13#10, False);
end;

{ ---------------- bootstrap (uv + Python 3.12 + paketler) ---------------- }

function StepText(const Id: String): String;
begin
  if Id = 'python' then Result := 'Python 3.12 indiriliyor ve hazırlanıyor…'
  else if Id = 'venv' then Result := 'Sanal ortam (.venv) oluşturuluyor…'
  else if Id = 'deps' then Result := 'Python paketleri kuruluyor (ilk kurulumda birkaç dakika sürebilir)…'
  else if Id = 'env' then Result := 'Ayar dosyası hazırlanıyor…'
  else if Id = 'model' then Result := 'Ses tanıma modeli indiriliyor (~460 MB)…'
  else if Id = 'tts' then Result := 'Ses sentezi hazırlanıyor…'
  else Result := 'Hazırlanıyor…';
end;

procedure BootstrapLog(const S: String; const Error, FirstLine: Boolean);
var
  Line: String;
begin
  Line := Trim(S);
  if Line = '' then Exit;
  Log('bootstrap: ' + Line);
  if Pos('@@STEP ', Line) = 1 then
  begin
    StepIndex := StepIndex + 1;
    ProgressPage.SetText(StepText(Copy(Line, 8, 64)), '');
    ProgressPage.SetProgress(StepIndex, StepTotal + 1);
  end
  else if Pos('@@ERROR ', Line) = 1 then
    BootstrapErrorTag := Copy(Line, 9, 64)
  else if Pos('@@', Line) <> 1 then
  begin
    if Length(Line) > 110 then Line := Copy(Line, 1, 107) + '…';
    ProgressPage.SetText(ProgressPage.Msg1Label.Caption, Line);
  end;
end;

function RunBootstrap: Integer;
var
  Params: String;
  Code: Integer;
begin
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\bootstrap.ps1') + '"';
  StepTotal := 4;
  if WizardIsTaskSelected('voicemodel') then
  begin
    Params := Params + ' -DownloadModels';
    StepTotal := 6;
  end;
  StepIndex := 0;
  BootstrapErrorTag := '';
  ProgressPage.SetText('Polaris hazırlanıyor…', '');
  ProgressPage.SetProgress(0, StepTotal + 1);
  Code := -1;
  try
    if not ExecAndLogOutput(PowerShellExe, Params, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code, @BootstrapLog) then
      Code := -1;
  except
    Log('bootstrap exec hatasi: ' + GetExceptionMessage);
    Code := -1;
  end;
  ProgressPage.SetProgress(StepTotal + 1, StepTotal + 1);
  Log(Format('bootstrap cikis kodu: %d (etiket: %s)', [Code, BootstrapErrorTag]));
  Result := Code;
end;

function BootstrapFailText: String;
begin
  if BootstrapResult = 2 then
    Result := 'Gerekli bileşenler indirilemedi: internet bağlantısı yok ya da sunucu şu an meşgul.' + #13#10#13#10 +
              'Bağlantınızı kontrol edip "Yeniden dene"ye basın. "Daha sonra" derseniz kurulum tamamlanır; ' +
              'Başlat menüsü → Polaris → "Polaris''i onar" ile kaldığı yerden devam edebilirsiniz.'
  else
    Result := 'Polaris ortamı hazırlanırken bir hata oluştu (' + BootstrapErrorTag + ').' + #13#10#13#10 +
              '"Yeniden dene" ile tekrar deneyin, "Ayrıntıları göster" ile kurulum günlüğünü açın ya da ' +
              '"Daha sonra" deyip Başlat menüsü → Polaris → "Polaris''i onar"ı çalıştırın.';
end;

{ Hata diyalogu: Yeniden dene / Ayrintilari goster / Daha sonra. True = tekrar dene. }
function AskRetry: Boolean;
var
  Labels: TArrayOfString;
  Answer, Code: Integer;
begin
  SetArrayLength(Labels, 3);
  Labels[0] := 'Yeniden dene';
  Labels[1] := 'Ayrıntıları göster';
  Labels[2] := 'Daha sonra';
  repeat
    Answer := SuppressibleTaskDialogMsgBox('Polaris hazırlanamadı', BootstrapFailText, mbError,
      MB_YESNOCANCEL, Labels, 0, IDCANCEL);
    if Answer = IDNO then
      ShellExec('', ExpandConstant('{win}\notepad.exe'), AddQuotes(ExpandConstant('{app}\logs\install.log')), '', SW_SHOWNORMAL, ewNoWait, Code);
  until Answer <> IDNO;
  Result := Answer = IDYES;
end;

{ ---------------- Brave ---------------- }

function WantBrave: Boolean;
begin
  Result := (BravePage <> nil) and (not HasBrave) and BravePage.Values[0] and (ExpandConstant('{param:NOBRAVE|0}') = '0');
end;

procedure InstallBrave;
var
  Winget, Setup: String;
  Code: Integer;
begin
  ProgressPage.SetText('Brave tarayıcısı kuruluyor…', 'Bu birkaç dakika sürebilir.');
  ProgressPage.SetProgress(0, 1);
  Winget := ExpandConstant('{localappdata}\Microsoft\WindowsApps\winget.exe');
  if FileExists(Winget) then
  begin
    Log('Brave: winget ile kuruluyor');
    Exec(Winget, 'install --id Brave.Brave -e --silent --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity',
      '', SW_HIDE, ewWaitUntilTerminated, Code);
    Log(Format('Brave winget cikis kodu: %d', [Code]));
  end;
  if not HasBrave then
  begin
    Log('Brave: resmi kurulum dosyasi indiriliyor');
    ProgressPage.SetText('Brave tarayıcısı indiriliyor…', 'brave.com');
    try
      DownloadTemporaryFile('https://laptop-updates.brave.com/latest/winx64', 'BraveBrowserSetup.exe', '', nil);
      Setup := ExpandConstant('{tmp}\BraveBrowserSetup.exe');
      ProgressPage.SetText('Brave tarayıcısı kuruluyor…', 'Bu birkaç dakika sürebilir.');
      Exec(Setup, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code);
      Log(Format('BraveBrowserSetup cikis kodu: %d', [Code]));
    except
      Log('Brave indirilemedi: ' + GetExceptionMessage);
    end;
  end;
  ProgressPage.SetProgress(1, 1);
  if not HasBrave then
    BraveNote := #13#10#13#10 + 'Brave otomatik kurulamadı; https://brave.com adresinden indirip kurabilirsiniz.';
end;

procedure InitializeWizard;
begin
  if not HasBrave then
  begin
    BravePage := CreateInputOptionPage(wpSelectTasks, 'Brave tarayıcısı',
      'Polaris, Brave tarayıcısının yan panelinde çalışır.',
      'Bu bilgisayarda Brave bulunamadı. Polaris''i kullanabilmek için Brave gerekir.', False, False);
    BravePage.Add('Brave tarayıcısını da kur (önerilir)');
    BravePage.Values[0] := True;
  end;
end;

{ Programlar ve Ozellikler'de gercek boyut (.venv + Python dahil). }
function DirSizeKB(const Dir: String): Int64;
var
  FR: TFindRec;
begin
  Result := 0;
  if FindFirst(AddBackslash(Dir) + '*', FR) then
  try
    repeat
      if (FR.Name <> '.') and (FR.Name <> '..') then
      begin
        if FR.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
        begin
          { NTFS baglanti noktalarini (reparse) izleme }
          if FR.Attributes and $400 = 0 then
            Result := Result + DirSizeKB(AddBackslash(Dir) + FR.Name);
        end
        else
          Result := Result + (Int64(FR.SizeHigh) * 4294967296 + FR.SizeLow) div 1024;
      end;
    until not FindNext(FR);
  finally
    FindClose(FR);
  end;
end;

procedure UpdateEstimatedSize;
var
  Key: String;
  KB: Int64;
begin
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{2CD58903-422C-437E-9A4F-650E698F0B0C}_is1';
  KB := DirSizeKB(ExpandConstant('{app}'));
  if KB > 0 then
  begin
    RegWriteDWordValue(HKCU, Key, 'EstimatedSize', Integer(KB));
    Log(Format('EstimatedSize: %d KB', [Integer(KB)]));
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    FillGuide;
    ProgressPage := CreateOutputProgressPage('Polaris hazırlanıyor',
      'Python 3.12 ve gerekli paketler indiriliyor. Sisteminizdeki Python''a dokunulmaz; her şey Polaris klasörüne kurulur.');
    ProgressPage.Show;
    try
      repeat
        BootstrapResult := RunBootstrap;
        if BootstrapResult = 0 then Break;
      until not AskRetry;
      if WantBrave then InstallBrave;
    finally
      ProgressPage.Hide;
    end;
    InstallDone := True;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then Result := Result + MemoTasksInfo + NewLine + NewLine;
  if WantBrave then Result := Result + 'Brave tarayıcısı da kurulacak.' + NewLine + NewLine;
  Result := Result + 'Kurulumdan sonra Python 3.12 ve gerekli paketler internetten indirilir' + NewLine +
            Space + '(sistem Python''u gerekmez; birkaç dakika sürebilir).';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { Yukseltme: eski surum calisiyorsa (tepsi + backend) dosyalar kilitli kalmasin. }
  StopPolarisProcesses(ExpandConstant('{app}'));
  Result := '';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    if BootstrapResult <> 0 then
      WizardForm.FinishedLabel.Caption := 'Polaris dosyaları kuruldu, ancak Python ortamı tamamlanamadı.' + #13#10#13#10 +
        'İnternet bağlantınızı kontrol edip Başlat menüsü → Polaris → "Polaris''i onar" kısayolunu çalıştırın.' + BraveNote
    else
      WizardForm.FinishedLabel.Caption := 'Polaris kuruldu.' + #13#10#13#10 +
        'Son adım: açılacak rehberdeki 5 kısa adımla Polaris''i Brave''e ekleyin. Polaris saatin yanındaki yıldız simgesinden yönetilir.' + BraveNote;
  end;
end;

procedure DeinitializeSetup;
begin
  if InstallDone then UpdateEstimatedSize;
end;

{ ---------------- kaldirma ---------------- }

procedure DeleteDirExcept(const Dir, KeepName: String);
var
  FR: TFindRec;
  Path: String;
begin
  if FindFirst(AddBackslash(Dir) + '*', FR) then
  try
    repeat
      if (FR.Name <> '.') and (FR.Name <> '..') and (CompareText(FR.Name, KeepName) <> 0) then
      begin
        Path := AddBackslash(Dir) + FR.Name;
        if FR.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
          DelTree(Path, True, True, True)
        else
          DeleteFile(Path);
      end;
    until not FindNext(FR);
  finally
    FindClose(FR);
  end;
end;

procedure RemoveRunValueIfOurs;
var
  Value: String;
begin
  if RegQueryStringValue(HKCU, '{#RunKey}', '{#RunValue}', Value) then
    if Pos(Lowercase(ExpandConstant('{app}')), Lowercase(Value)) > 0 then
      RegDeleteValue(HKCU, '{#RunKey}', '{#RunValue}');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  App: String;
begin
  App := ExpandConstant('{app}');
  if CurUninstallStep = usUninstall then
  begin
    StopPolarisProcesses(App);
    KeepUserData := False;
    if DirExists(App + '\backend\data') then
      KeepUserData := SuppressibleMsgBox(
        'Konuşma geçmişi ve kayıtlar (backend\data) saklansın mı?' + #13#10#13#10 +
        'Evet: yalnızca bu klasör kalır (yeniden kurulumda kullanılır).' + #13#10 +
        'Hayır: her şey silinir.' + #13#10#13#10 +
        'API anahtarı (backend\.env) her durumda silinir.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
    RemoveRunValueIfOurs;
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    { .venv, runtime (Python + uv onbellegi), logs, backend\.env vb. Inno'nun bilmedigi her sey. }
    if KeepUserData then
    begin
      DeleteDirExcept(App, 'backend');
      DeleteDirExcept(App + '\backend', 'data');
    end
    else
      DelTree(App, True, True, True);
  end;
end;
