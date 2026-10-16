; Golden Music — Windows installer script (Inno Setup 6)
; Build:  iscc goldenmusic.iss            (uses the default AppVersion below)
;         iscc /DAppVersion=x.y.z goldenmusic.iss   (CI — must match version.py)
; Output: dist/installer/GoldenMusicSetup-<version>.exe
;
; Update behavior:
;   - AppId is the SAME as v1.0.0, so Windows/Inno treat this as an UPDATE
;     of an existing installation (default dir pre-filled from registry,
;     old files replaced, user config in %USERPROFILE%\.goldenmusic kept).
;   - For machines without a previous install it behaves as a normal
;     first-time install (per-user, no admin required).

#define AppName        "Golden Music"
; AppVersion default — CI passes /DAppVersion= from version.py; when building
; by hand this default must match version.py (scripts/test_version_sync.py
; asserts the agreement, so a drift breaks CI instead of shipping).
#ifndef AppVersion
#define AppVersion     "2.1.1"
#endif
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
OutputBaseFilename=GoldenMusicSetup-{#AppVersion}
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
; Same mutex name as main.pyw's CreateMutex — lets the installer detect a
; running app on upgrade instead of failing or demanding a reboot.
AppMutex=GoldenMusic_SingleInstance_Mutex
; Offer to close the running app during install/uninstall (uses AppMutex).
CloseApplications=yes
ChangesAssociations=yes
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
Name: "fileassoc"; Description: "Add Golden Music to 'Open with' for audio files"; \
    GroupDescription: "Integration:"

; "Open with" integration only — the user's default player is NEVER
; changed silently; file types are associated exclusively through the
; OpenWithProgids mechanism (adds an entry to the Open with menu).
[Registry]
; ProgID describing audio files opened with Golden Music
Root: HKA; Subkey: "Software\Classes\GoldenMusic.AudioFile"; \
    ValueType: string; ValueName: "FriendlyTypeName"; \
    ValueData: "Audio file (Golden Music)"; Flags: uninsdeletekey; \
    Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\GoldenMusic.AudioFile\DefaultIcon"; \
    ValueType: string; ValueData: "{app}\assets\icon.ico"; \
    Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\GoldenMusic.AudioFile\shell\open\command"; \
    ValueType: string; ValueData: """{app}\{#AppExeName}"" ""%1"""; \
    Flags: uninsdeletekey; Tasks: fileassoc
; Make the exe appear in the Open with dialog for every supported type
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}"; \
    ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; \
    Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}\shell\open\command"; \
    ValueType: string; ValueData: """{app}\{#AppExeName}"" ""%1"""; \
    Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExeName}"; \
    ValueType: string; ValueName: "SupportedTypes"; \
    ValueData: ".mp3 .wav .flac .ogg .oga .opus .m4a .aac .wma"; \
    Flags: uninsdeletevalue; Tasks: fileassoc
; OpenWithProgids per extension — the safe, non-hijacking association
Root: HKA; Subkey: "Software\Classes\.mp3\OpenWithProgids"; \
    ValueType: string; ValueName: "GoldenMusic.AudioFile"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.wav\OpenWithProgids"; \
    ValueType: string; ValueName: "GoldenMusic.AudioFile"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.flac\OpenWithProgids"; \
    ValueType: string; ValueName: "GoldenMusic.AudioFile"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.ogg\OpenWithProgids"; \
    ValueType: string; ValueName: "GoldenMusic.AudioFile"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.oga\OpenWithProgids"; \
    ValueType: string; ValueName: "GoldenMusic.AudioFile"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.opus\OpenWithProgids"; \
    ValueType: string; ValueName: "GoldenMusic.AudioFile"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.m4a\OpenWithProgids"; \
    ValueType: string; ValueName: "GoldenMusic.AudioFile"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.aac\OpenWithProgids"; \
    ValueType: string; ValueName: "GoldenMusic.AudioFile"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\.wma\OpenWithProgids"; \
    ValueType: string; ValueName: "GoldenMusic.AudioFile"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: fileassoc

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

var
  // Set in InitializeSetup (BEFORE anything is changed) and consumed in
  // CurStepChanged — true only when a real v1.0.0 legacy install exists.
  LegacyExists: Boolean;
  LegacyDir: String;

function GetLegacyInstallDir(): String;
var
  D: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + LegacyKey,
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

  // Snapshot the legacy state NOW — before this run can modify anything.
  LegacyExists := RegKeyExists(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + LegacyKey);
  if LegacyExists then
    LegacyDir := GetLegacyInstallDir();

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
begin
  if CurStep = ssPostInstall then
  begin
    // Migration cleanup: v1.0.0's uninstaller entry used the malformed GUID.
    // Remove that stale entry so Windows shows only ONE installed app.
    if LegacyExists then
    begin
      RegDeleteKeyIncludingSubkeys(HKCU,
        'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + LegacyKey);
      Log('Migrated legacy uninstall key');
    end;

    // Delete the LEGACY uninstaller only when the legacy record really
    // existed AND it points somewhere else than {app}. The previous code
    // ran on EVERY install where unins000.exe was next to the recorded
    // dir — including a normal update-in-place, where it would delete the
    // uninstaller Inno just wrote for THIS install.
    if LegacyExists and (LegacyDir <> '') and
       (CompareText(LegacyDir, ExpandConstant('{app}')) <> 0) then
    begin
      LegacyUninst := AddBackslash(LegacyDir) + 'unins000.exe';
      if FileExists(LegacyUninst) then
      begin
        DelTree(ExtractFilePath(LegacyUninst) + 'unins*', True, True, False);
        Log('Removed stale legacy uninstaller files in ' + LegacyDir);
      end;
    end;
  end;
end;
