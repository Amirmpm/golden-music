; Golden Music — Windows installer script (Inno Setup 6)
; Build:  iscc goldenmusic.iss
; Output: dist/installer/GoldenMusicSetup-2.1.1.exe
;
; Update behavior:
;   - AppId is the SAME as v1.0.0, so Windows/Inno treat this as an UPDATE
;     of an existing installation (default dir pre-filled from registry,
;     old files replaced, user config in %USERPROFILE%\.goldenmusic kept).
;   - For machines without a previous install it behaves as a normal
;     first-time install (per-user, no admin required).

#define AppName        "Golden Music"
#define AppVersion     "2.1.1"
#define AppPublisher   "Golden Music"
#define AppExeName     "GoldenMusic.exe"
#define AppDescription "Golden Music Player"

[Setup]
; MUST stay identical across releases — this GUID ties installs together.
AppId={{9F5E7C8D-3B2E-5C4F-AC6D-2B3C4D5E6F7A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
; Remember where the previous version lives and offer it as default
UsePreviousAppDir=yes
DirExistsWarning=no
AppendDefaultDirName=no
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
OutputDir=dist\installer
OutputBaseFilename=GoldenMusicSetup-2.1.1
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; IS7 users may uncomment the next line to build the Setup binary itself
; as 64-bit (matches the x64-only payload). It requires Inno Setup 7+;
; with Inno Setup 6 (e.g. the GitHub CI action) it must stay commented —
; IS6 then produces the classic 32-bit Setup runtime, which works the
; same on Windows.
;SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppDescription}
SetupIconFile=assets\icon.ico
; Never touch per-user data on update/upgrade
UninstallFilesDir={app}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; \
    GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start with Windows (optional)"; \
    GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\GoldenMusic\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\icon.ico"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\assets\icon.ico"; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; \
    Flags: nowait postinstall skipifsilent

[Code]
const
  // Current (fixed) AppId key suffix
  NewKey = '{9F5E7C8D-3B2E-5C4F-AC6D-2B3C4D5E6F7A}_is1';
  // v1.0.0 shipped a malformed GUID ('5G4F' is not valid hex); installs made
  // with it registered under that key instead. We detect AND migrate them.
  LegacyKey = '{9F5E7C8D-3B2E-5G4F-AC6D-2B3C4D5E6F7A}_is1';

function GetInstalledVersion(): String;
var
  V: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + NewKey,
      'DisplayVersion', V) or
     RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + NewKey,
      'DisplayVersion', V) or
     // legacy v1.0.0 installs (malformed GUID)
     RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + LegacyKey,
      'DisplayVersion', V) then
    Result := V;
end;

function GetExistingInstallDir(): String;
var
  D: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + NewKey,
      'InstallLocation', D) or
     RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + NewKey,
      'InstallLocation', D) or
     RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + LegacyKey,
      'InstallLocation', D) or
     RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + LegacyKey,
      'Inno Setup: App Path', D) then
    Result := RemoveBackslashUnlessRoot(D);
end;

function InitializeSetup(): Boolean;
var
  OldVersion, Msg: String;
begin
  Result := True;

  OldVersion := GetInstalledVersion();
  // Ask only in interactive mode — /SILENT and /VERYSILENT skip the prompt
  // and update unconditionally.
  if (not WizardSilent()) and (OldVersion <> '') then
  begin
    Msg := 'Golden Music ' + OldVersion + ' is already installed.' #13#10 +
           'Setup will UPDATE it to version {#AppVersion}.' #13#10 #13#10 +
           'Your library, favorites and settings are preserved.' #13#10 +
           'Continue?';
    if MsgBox(Msg, mbInformation, MB_YESNO) = IDNO then
    begin
      Result := False;
      exit;
    end;
  end;
end;

// Make Inno treat a legacy install as "previous version" for dir picking:
// seed the wizard's directory when the registry has a legacy record.
procedure InitializeWizard();
var
  Dir: String;
begin
  if (GetInstalledVersion() <> '') and (GetExistingInstallDir() <> '') then
  begin
    Dir := GetExistingInstallDir();
    if DirExists(Dir) then
      WizardForm.DirEdit.Text := Dir;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  LegacyUninst: String;
  InstallDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    // Migration cleanup: v1.0.0's uninstaller entry used the malformed GUID.
    // Remove that stale entry so Windows shows only ONE installed app.
    InstallDir := GetExistingInstallDir();
    if RegKeyExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + LegacyKey) then
    begin
      RegDeleteKeyIncludingSubkeys(HKCU,
        'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + LegacyKey);
      Log('Migrated legacy uninstall key');
    end;

    // If the legacy folder has an old unins000.exe pointing elsewhere but we
    // just updated IN that folder, its files were replaced by ours anyway.
    // The stale uninstaller binary is removed to avoid double-uninstall paths.
    if InstallDir <> '' then
    begin
      LegacyUninst := AddBackslash(InstallDir) + 'unins000.exe';
      if FileExists(LegacyUninst) then
        DelTree(ExtractFilePath(LegacyUninst) + 'unins*', True, True, False);
    end;
  end;
end;
