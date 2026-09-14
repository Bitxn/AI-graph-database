; OnePort installer — Inno Setup script.
; Build:  install Inno Setup (https://jrsoftware.org/isdl.php), open this file,
;         then Build → Compile.  Output: OnePort-Setup.exe (next to this file).
;
; Installs per-user (no admin needed), creates Start Menu + optional Desktop
; shortcuts, and registers an uninstaller. Ships the whole self-contained app
; folder (all 18 tools bundled).

#define AppName    "OnePort"
#define AppVersion "0.2.4"
#define AppExe     "OnePort.exe"

[Setup]
AppId={{B9E4B0A2-6C1E-4E5A-9C21-0A1B2C3D4E5F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=OnePort
AppPublisherURL=https://oneport.co.in
DefaultDirName={localappdata}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=OnePort-Setup
SetupIconFile=tauri\src-tauri\icons\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; the entire self-contained onedir build
Source: "dist\OnePort\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}";              Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}";    Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";        Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent
