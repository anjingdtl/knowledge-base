; ShineHeKnowledge Inno Setup 安装脚本
; 用法: ISCC installer\setup.iss
; 需要安装 Inno Setup: https://jrsoftware.org/isdl.php

#define AppName "ShineHeKnowledge"
#define AppVersion GetVersionNumbersString("..\\src\\version.py")
#define AppPublisher "ShineHe"
#define AppExeName "ShineHeKnowledge.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename={#AppName}_v{#AppVersion}_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile=
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"

[Files]
Source: "..\dist\ShineHeKnowledge.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.yaml"; DestDir: "{app}"; Flags: confirmoverwrite
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[Dirs]
Name: "{app}\data"
