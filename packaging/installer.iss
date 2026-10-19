; Installateur Windows (Inno Setup 6), appelé par packaging/build.py avec /DAppVersion=x.y.z
; Installation pour l'utilisateur courant, sans droits administrateur. La désinstallation garde la bibliothèque
; (%APPDATA%\dj-music-manager) et la clé API (Gestionnaire d'identifiants).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{4C46D1F9-CC32-4A8A-B795-9CFDF335DE20}
AppName=DJ Music Manager
AppVersion={#AppVersion}
AppPublisher=mathr81
AppPublisherURL=https://github.com/Mathr81/dj-music-manager
DefaultDirName={autopf}\DJ Music Manager
DefaultGroupName=DJ Music Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=DJ-Music-Manager-{#AppVersion}-setup
SetupIconFile=..\src\dj_music_manager\icon.ico
UninstallDisplayIcon={app}\dj-music-manager.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; ignoreversion : une mise à jour remplace tout le dossier de l'app
Source: "..\dist\dj-music-manager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; fichiers d'une version précédente qui n'existeraient plus
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\DJ Music Manager"; Filename: "{app}\dj-music-manager.exe"
Name: "{autodesktop}\DJ Music Manager"; Filename: "{app}\dj-music-manager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\dj-music-manager.exe"; Description: "{cm:LaunchProgram,DJ Music Manager}"; Flags: nowait postinstall skipifsilent
