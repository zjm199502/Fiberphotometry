; Inno Setup script: wraps the PyInstaller folder (dist/fiberphoto) into a one-click Setup.exe.
;   iscc /DAppVersion=0.3.1 packaging\windows_installer.iss
; Installs per user (no administrator rights needed) and adds Start-menu and optional desktop shortcuts.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B8674D61-9936-4959-9024-677E18A012C3}
AppName=FiberPhoto
AppVersion={#AppVersion}
AppPublisher=FiberPhoto
DefaultDirName={autopf}\FiberPhoto
DefaultGroupName=FiberPhoto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\installer
OutputBaseFilename=FiberPhoto-{#AppVersion}-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\fiberphoto.exe

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\fiberphoto\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\FiberPhoto"; Filename: "{app}\fiberphoto.exe"
Name: "{group}\FiberPhoto Manual (Chinese)"; Filename: "{app}\_internal\fiberphoto\gui\manual\manual_zh.pdf"
Name: "{group}\FiberPhoto Manual (English)"; Filename: "{app}\_internal\fiberphoto\gui\manual\manual_en.pdf"
Name: "{group}\{cm:UninstallProgram,FiberPhoto}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\FiberPhoto"; Filename: "{app}\fiberphoto.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\fiberphoto.exe"; Description: "{cm:LaunchProgram,FiberPhoto}"; Flags: nowait postinstall skipifsilent
