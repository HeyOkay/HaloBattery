; Inno Setup script for HaloBattery-<version>-setup.exe.
;
; Build (after PyInstaller has made dist\HaloBattery):
;   iscc /DAppVersion=1.17.0 installer\HaloBattery.iss
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
#if (Ver & 0xFFFFFF00) != (EncodeVer(6, 7, 4) & 0xFFFFFF00)
  #error Use the pinned Inno Setup 6.7.4 compiler from scripts/get_inno.ps1.
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
AppPublisherURL=https://github.com/HeyOkay/HaloBattery
AppSupportURL=https://github.com/HeyOkay/HaloBattery/issues
AppUpdatesURL=https://github.com/HeyOkay/HaloBattery/releases
VersionInfoVersion={#AppVersion}
VersionInfoProductName=HaloBattery
VersionInfoProductVersion={#AppVersion}
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
; Updated program libraries may not be in the original uninstall log. User data
; (portable.txt, config/history, iconify and settings-window.json) stays at {app}.
; Never recursively delete the install root: it can contain the user's own files.
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

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

{ Native handle types follow the installer bitness, including 32-bit setup on
  64-bit Windows. DWORD process IDs and buffer lengths remain 32-bit.
  Sources:
  https://jrsoftware.org/ishelp/topic_scriptdll.htm
  https://learn.microsoft.com/windows/win32/api/psapi/nf-psapi-enumprocesses
  https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-queryfullprocessimagenamew
  https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess }
type
  TProcessIds = array of Cardinal;
  TProcessHandles = array of THandle;

function InstallerEnumProcesses(var FirstId: Cardinal; BufferBytes: Cardinal;
  var BytesUsed: Cardinal): BOOL;
  external 'K32EnumProcesses@kernel32.dll stdcall';
function InstallerOpenProcess(Access: Cardinal; Inherit: BOOL; Id: Cardinal): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function InstallerQueryImage(Process: THandle; Flags: Cardinal; Image: String;
  var Characters: Cardinal): BOOL;
  external 'QueryFullProcessImageNameW@kernel32.dll stdcall';
function InstallerTerminateProcess(Process: THandle; ExitCode: Cardinal): BOOL;
  external 'TerminateProcess@kernel32.dll stdcall';
function InstallerWait(Process: THandle; Milliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function InstallerCloseHandle(Process: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';
function InstallerTicks(): Int64;
  external 'GetTickCount64@kernel32.dll stdcall';

function IsInstallProcess(Process: THandle): Boolean;
var
  Image: String;
  Characters: Cardinal;
begin
  Result := False;
  Characters := 32768;
  Image := StringOfChar(#0, Characters);
  if not InstallerQueryImage(Process, 0, Image, Characters) then
    Exit;                         { Unknown identity must never authorize a kill. }
  SetLength(Image, Characters);
  Image := ExpandFileName(Image);
  Result := (CompareText(Image, ExpandFileName(ExpandConstant('{app}\{#AppExe}'))) = 0) or
            (CompareText(Image, ExpandFileName(ExpandConstant(
              '{app}\_internal\settings-ui\HaloBattery.Settings.exe'))) = 0);
end;

function CloseApp(): String;
var
  Ids: TProcessIds;
  Handles: TProcessHandles;
  BytesUsed, BufferBytes: Cardinal;
  I, IdCount, HandleCount: Integer;
  Process: THandle;
  Deadline, Remaining: Int64;
  ErrorText: String;
begin
  Result := '';
  ErrorText := 'Unable to close Halo Battery in ' + ExpandConstant('{app}') + '.' + #13#10 +
    'Close this copy of Halo Battery and its Settings window, then try again.';
  SetArrayLength(Handles, 0);
  try
    try
      SetArrayLength(Ids, 1024);
      repeat
        BufferBytes := GetArrayLength(Ids) * 4;
        if not InstallerEnumProcesses(Ids[0], BufferBytes, BytesUsed) then
        begin
          Result := ErrorText;
          Exit;
        end;
        if BytesUsed < BufferBytes then
          Break;
        if GetArrayLength(Ids) >= 65536 then
        begin
          Result := ErrorText;     { An incomplete process list is not success. }
          Exit;
        end;
        SetArrayLength(Ids, GetArrayLength(Ids) * 2);
      until False;
      IdCount := BytesUsed div 4;
      for I := 0 to IdCount - 1 do
      begin
        { Query, terminate and wait use this same pinned process handle, so PID
          reuse cannot redirect termination to an unrelated portable copy. }
        Process := InstallerOpenProcess($00101001, False, Ids[I]);
        if Process <> 0 then
        begin
          try
            if IsInstallProcess(Process) then
            begin
              HandleCount := GetArrayLength(Handles);
              SetArrayLength(Handles, HandleCount + 1);
              Handles[HandleCount] := Process;
              Process := 0;       { Transfer ownership to the final cleanup below. }
            end;
          finally
            if Process <> 0 then
              InstallerCloseHandle(Process);
          end;
        end;
      end;
      for I := 0 to GetArrayLength(Handles) - 1 do
        if InstallerWait(Handles[I], 0) <> 0 then
          if not InstallerTerminateProcess(Handles[I], 0) then
            if InstallerWait(Handles[I], 0) <> 0 then
              Result := ErrorText;
      { TerminateProcess is asynchronous. One shared deadline bounds waiting
        for both verified executable paths, including Settings. }
      Deadline := InstallerTicks() + 5000;
      for I := 0 to GetArrayLength(Handles) - 1 do
      begin
        Remaining := Deadline - InstallerTicks();
        if Remaining < 0 then
          Remaining := 0;
        if InstallerWait(Handles[I], Cardinal(Remaining)) <> 0 then
          Result := ErrorText;
      end;
    except
      Log('Closing this install failed: ' + GetExceptionMessage);
      Result := ErrorText;
    end;
  finally
    for I := 0 to GetArrayLength(Handles) - 1 do
      InstallerCloseHandle(Handles[I]);
  end;
  { No name-wide fallback: an unqueryable process is left alone; normal file-in-
    use errors remain available if it holds this install's files open. }
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := CloseApp();
end;

function InitializeUninstall(): Boolean;
var
  ErrorText: String;
begin
  ErrorText := CloseApp();
  Result := ErrorText = '';
  if not Result then
    SuppressibleMsgBox(ErrorText, mbError, MB_OK, IDOK);
end;

function RunValueBelongsToInstall(const Cmd: String): Boolean;
begin
  { Both setup and the frozen app write one quoted executable, without args.
    Preserve portable commands and unfamiliar/manual command lines. }
  Result := CompareText(Trim(Cmd), '"' + ExpandConstant('{app}\{#AppExe}') + '"') = 0;
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
     RunValueBelongsToInstall(Cmd) then
    RegDeleteValue(HKCU, '{#RunKey}', 'HaloBattery');
  RegDeleteKeyIncludingSubkeys(HKCU, 'Software\Classes\AppUserModelId\HaloBattery');
  Data := ExpandConstant('{userappdata}\HaloBattery');
  if DirExists(Data) and not UninstallSilent then
    if MsgBox('Also delete your Halo Battery settings and battery history?' + #13#10 + Data,
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(Data, True, True, True);
end;
