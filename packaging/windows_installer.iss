; Inno Setup script: wraps the PyInstaller folder (dist/fiberphoto) into a one-click Setup.exe.
;   iscc /DAppVersion=0.3.1 packaging\windows_installer.iss
; Installs per user (no administrator rights needed) and adds Start-menu and optional desktop shortcuts.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B8674D61-9936-4959-9024-677E18A012C3}
AppName=FiberLume
AppVersion={#AppVersion}
AppPublisher=FiberLume
DefaultDirName={autopf}\FiberPhoto
DefaultGroupName=FiberLume
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\installer
OutputBaseFilename=FiberLume-{#AppVersion}-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=fiberlume.ico
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\fiberphoto.exe

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\fiberphoto\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{group}\FiberPhoto.lnk"
Type: files; Name: "{group}\FiberPhoto Manual (Chinese).lnk"
Type: files; Name: "{group}\FiberPhoto Manual (English).lnk"
Type: files; Name: "{autodesktop}\FiberPhoto.lnk"

[Icons]
Name: "{group}\FiberLume"; Filename: "{app}\fiberphoto.exe"
Name: "{group}\FiberLume Manual (Chinese)"; Filename: "{app}\_internal\fiberphoto\gui\manual\manual_zh.pdf"
Name: "{group}\FiberLume Manual (English)"; Filename: "{app}\_internal\fiberphoto\gui\manual\manual_en.pdf"
Name: "{group}\{cm:UninstallProgram,FiberLume}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\FiberLume"; Filename: "{app}\fiberphoto.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\fiberphoto.exe"; Description: "{cm:LaunchProgram,FiberLume}"; Flags: nowait postinstall skipifsilent
