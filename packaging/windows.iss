#ifndef AppVersion
  #error AppVersion must be supplied by build_desktop.py
#endif

[Setup]
AppId={{C65B2CA9-4A8E-48DC-8B58-4BE3F5C2DCC0}
AppName=8T: 8 Track DAW (Preview)
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\8T
DefaultGroupName=8T
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ReleaseDir}
OutputBaseFilename={#ReleaseName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\8t.exe

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\8T"; Filename: "{app}\8t.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\8t.exe"; Description: "Launch 8T"; Flags: nowait postinstall skipifsilent