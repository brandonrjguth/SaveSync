; SaveSync Windows installer. Build with build\windows.ps1 after PyInstaller has
; produced dist\SaveSync\. Installs per user, so no administrator prompt is needed.

#define SaveSyncName "SaveSync"
#ifndef SaveSyncVersion
#define SaveSyncVersion "1.2.0"
#endif

[Setup]
AppId={{7C1A5B92-93D4-4E86-9A47-1D3F4F1D9C42}
AppName={#SaveSyncName}
AppVersion={#SaveSyncVersion}
AppPublisher={#SaveSyncName}
DefaultDirName={autopf}\{#SaveSyncName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\SaveSync.exe
OutputDir=..\dist
OutputBaseFilename=SaveSync-Setup
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\SaveSync\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#SaveSyncName}"; Filename: "{app}\SaveSync.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#SaveSyncName}"; Filename: "{app}\SaveSync.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\SaveSync.exe"; Description: "Launch {#SaveSyncName}"; Flags: nowait postinstall skipifsilent
