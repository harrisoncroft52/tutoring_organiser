; Inno Setup 6 script. Build with:  iscc /DMyAppVersion=1.0.0 installer.iss
; Run AFTER PyInstaller has produced dist\TutoringOrganiser\.
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppName "Tutoring Organiser"
#define MyAppExe "TutoringOrganiser.exe"

[Setup]
; Never change AppId: it is how a new installer recognises and upgrades an existing install.
AppId={{4E11BD48-85CD-4874-A5F1-830AC71D5564}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Harrison Croft
AppPublisherURL=https://github.com/harrisoncroft52/tutoring_organiser
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install: no admin prompt, and silent updates work without UAC.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer_output
OutputBaseFilename=TutoringOrganiser-Setup-{#MyAppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "dist\TutoringOrganiser\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
; Normal installs: offer "Launch" on the last page.
Filename: "{app}\{#MyAppExe}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
; Silent installs (= the in-app updater): relaunch automatically.
Filename: "{app}\{#MyAppExe}"; Flags: nowait; Check: WizardSilent

; Students, lessons and Google sign-in files live in %APPDATA%\TutoringOrganiser,
; outside {app}, so neither updating nor uninstalling touches them.
