; Inno Setup script for HaloBattery-<version>-setup.exe.
;
; Build (after PyInstaller has made dist\HaloBattery):
;   iscc /DAppVersion=1.16.0 installer\HaloBattery.iss
; The setup lands in dist\.
;
; Per-user install, no admin prompt: the app goes to %LOCALAPPDATA%\Programs\HaloBattery.
; That folder stays writable, so the in-app updater can keep replacing the files.
; Registry: the uninstall entry (Settings > Apps) under HKCU, and the optional
; "Start with Windows" value under HKCU\...\Run - the same value the app's own
; menu toggle writes. Uninstall removes both (the Run value only if it points at this
; install, not at a portable copy elsewhere), the app's notification registration,
; and asks before deleting the settings in %APPDATA%\HaloBattery.

#ifndef AppVersion
  #error Pass the version: iscc /DAppVersion=1.2.3 installer\HaloBattery.iss
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\HaloBattery"
#endif

#define AppName "Halo Battery"
#define AppExe "HaloBattery.exe"
#define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"

[Setup]
; Never change AppId: it is how setup finds an existing install to upgrade.
AppId={{1E3A3904-9F39-4150-82EA-25D23968E2D6}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=HaloBattery contributors
AppPublisherURL=https://github.com/ahmedkhursheed23/HaloBattery
AppSupportURL=https://github.com/ahmedkhursheed23/HaloBattery/issues
AppUpdatesURL=https://github.com/ahmedkhursheed23/HaloBattery/releases
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\HaloBattery
DisableProgramGroupPage=yes
DefaultGroupName={#AppName}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\halo.ico
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=HaloBattery-{#AppVersion}-setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The tray app is closed by PrepareToInstall below; Restart Manager is not needed.
CloseApplications=no

[Tasks]
Name: "autostart"; Description: "Start Halo Battery when I sign in to Windows"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "HaloBattery"; ValueData: """{app}\{#AppExe}"""; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; Upgrade or downgrade: start from a clean library folder, so no file of the old
; version is left next to the new one (PyInstaller mixes them up).
Type: filesandordirs; Name: "{app}\_internal"

[UninstallDelete]
; The in-app updater can add files that setup never installed.
Type: filesandordirs; Name: "{app}"

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{1E3A3904-9F39-4150-82EA-25D23968E2D6}_is1';

{ Every version shares the AppId, so there is only ever one install, one folder and one
  uninstall entry: a newer or the same version upgrades or repairs it in place. An older
  setup asks first; silent (/VERYSILENT) it refuses with exit code 1 unless /ALLOWDOWNGRADE
  is given. The installed version is read from the uninstall entry, which the app keeps
  current after an in-app update. }
function HasParam(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

function InitializeSetup(): Boolean;
var
  Installed: String;
  Have, Want: Int64;
begin
  Result := True;
  if not RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', Installed) then
    Exit;
  if not (StrToVersion(Installed, Have) and StrToVersion('{#AppVersion}', Want)) then
    Exit;
  if ComparePackedVersion(Have, Want) <= 0 then
    Exit;
  if WizardSilent then
  begin
    Result := HasParam('/ALLOWDOWNGRADE');
    if not Result then
      Log('Halo Battery ' + Installed + ' is installed; not downgrading to {#AppVersion} without /ALLOWDOWNGRADE');
  end
  else
    Result := MsgBox('Halo Battery ' + Installed + ' is installed, which is newer than {#AppVersion}.' + #13#10 +
                     'Replace it with the older version? Your settings are kept.',
                     mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CloseApp();
var
  Code: Integer;
begin
  { The tray app has no window for Restart Manager to close. Only this user's copy. }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im {#AppExe} /fi "USERNAME eq ' + GetUserNameString + '"',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(500);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  CloseApp();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data, Cmd: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  { Also when "Start with Windows" was turned on from the app's menu, not by setup -
    but not when it points at a portable copy somewhere else. }
  if RegQueryStringValue(HKCU, '{#RunKey}', 'HaloBattery', Cmd) and
     (Pos(Lowercase(ExpandConstant('{app}\')), Lowercase(Cmd)) > 0) then
    RegDeleteValue(HKCU, '{#RunKey}', 'HaloBattery');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\AppUserModelId\HaloBattery');
  Data := ExpandConstant('{userappdata}\HaloBattery');
  if DirExists(Data) and not UninstallSilent then
    if MsgBox('Also delete your Halo Battery settings and battery history?' + #13#10 + Data,
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(Data, True, True, True);
end;
