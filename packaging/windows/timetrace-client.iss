#define AppId "VanillaYukirin.TimeTrace.Client"
#define AppName "TimeTrace Client"
#define AppExe AppName + ".exe"
#define BrandIcon "_internal\timetrace\client\dashboard\timetrace-kuro.ico"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Vanilla-Yukirin
AppPublisherURL=https://github.com/Vanilla-Yukirin/TimeTrace
AppUpdatesURL=https://github.com/Vanilla-Yukirin/TimeTrace/releases/latest
DefaultDirName={localappdata}\Programs\TimeTrace
DefaultGroupName=TimeTrace
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern dynamic
AppMutex=Local\{#AppId},Local\{#AppId}.Dashboard
CloseApplications=yes
RestartApplications=no
UsePreviousTasks=no
UninstallDisplayIcon={app}\{#BrandIcon}
SetupIconFile={#AppIcon}
OutputDir={#OutputDir}
OutputBaseFilename=TimeTrace-Client-Setup-x64
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName={#AppName}
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: autostart; Description: "Start TimeTrace when I sign in"

[Files]
Source: "{#PackageDir}\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PackageDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--gui"; WorkingDir: "{app}"; AppUserModelID: "{#AppId}"; IconFilename: "{app}\{#BrandIcon}"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: autostart; IconFilename: "{app}\{#BrandIcon}"

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--gui"; Description: "Open TimeTrace"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{userstartup}\{#AppName}.lnk"
; The old ZIP installed bundled files here without an uninstall log.
Type: filesandordirs; Name: "{app}\_internal"

[Code]
var
  StartupTaskInitialized: Boolean;

function StartupShortcut: String;
begin
  Result := ExpandConstant('{userstartup}\{#AppName}.lnk');
end;

procedure CurPageChanged(PageID: Integer);
var
  Index: Integer;
  Argument: String;
begin
  if (PageID <> wpSelectTasks) or StartupTaskInitialized then
    Exit;
  StartupTaskInitialized := True;
  for Index := 1 to ParamCount do
  begin
    Argument := Uppercase(ParamStr(Index));
    if (Pos('/TASKS=', Argument) = 1) or (Pos('/MERGETASKS=', Argument) = 1) then
      Exit;
  end;
  // The dashboard can change the shortcut between installations.
  if FileExists(AddBackslash(WizardDirValue) + '{#AppExe}') and not FileExists(StartupShortcut) then
    WizardSelectTasks('!autostart');
end;

procedure CurStepChanged(Step: TSetupStep);
begin
  if Step = ssPostInstall then
  begin
    if not WizardIsTaskSelected('autostart') then
      DeleteFile(StartupShortcut);
    DeleteFile(ExpandConstant('{group}\Uninstall TimeTrace.lnk'));
    DeleteFile(ExpandConstant('{app}\Install TimeTrace.ps1'));
    DeleteFile(ExpandConstant('{app}\Uninstall TimeTrace.ps1'));
  end;
end;
