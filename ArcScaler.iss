; Build with build_installer.py and Inno Setup 6.6 or newer.
[Setup]
AppId=ArcScaler.Desktop
AppName=ArcScaler
AppVersion=1.1.0
AppPublisher=ArcScaler
LicenseFile=LICENSE.txt
WizardStyle=modern dark
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=ArcScaler
#ifdef SignBuild
SignTool=arcscaler
SignedUninstaller=yes
#endif
DefaultDirName={localappdata}\Programs\ArcScaler
DefaultGroupName=ArcScaler
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=ArcScaler-Setup
SetupIconFile=assets\arcscaler.ico
UninstallDisplayIcon={app}\ArcScaler.exe
Compression=lzma2
SolidCompression=yes
[Files]
Source: "dist\ArcScaler.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\versions\v0.9.4\Licenses\*"; DestDir: "{app}\Licenses\OptiScaler"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\ArcScaler-source.zip"; DestDir: "{app}"; Flags: ignoreversion
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Icons]
Name: "{group}\ArcScaler"; Filename: "{app}\ArcScaler.exe"; IconFilename: "{app}\ArcScaler.exe"
Name: "{autodesktop}\ArcScaler"; Filename: "{app}\ArcScaler.exe"; IconFilename: "{app}\ArcScaler.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\ArcScaler.exe"; Description: "Launch ArcScaler"; Flags: nowait postinstall skipifsilent
