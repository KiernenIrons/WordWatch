#ifndef PayloadDir
  #define PayloadDir "payload"
#endif
#ifndef ReleaseDir
  #define ReleaseDir "artifacts"
#endif
[Setup]
AppId={{48C37688-626E-4ABD-AB9D-D893F436C550}
AppName=WordWatch
AppVersion=1.0.0
AppPublisher=WordWatch contributors
AppPublisherURL=https://github.com/KiernenIrons/WordWatch
DefaultDirName={localappdata}\Programs\WordWatch
DefaultGroupName=WordWatch
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ReleaseDir}
OutputBaseFilename=WordWatch-1.0.0-Setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\WordWatch.exe
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
LicenseFile=LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; Flags: checkedonce

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\WordWatch"; Filename: "{app}\WordWatch.exe"
Name: "{autodesktop}\WordWatch"; Filename: "{app}\WordWatch.exe"; Tasks: desktopicon
