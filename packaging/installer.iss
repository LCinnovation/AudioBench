; AudioBench 安装包（Inno Setup 6）
; 默认 per-user 装到 %LOCALAPPDATA%\Programs\AudioBench（不需要管理员，CrossOver 瓶内也最稳），
; 安装向导允许选「为所有用户安装」。
; 构建： pwsh -File packaging\make_installer.ps1

#define MyAppName "AudioBench"
#define MyAppVersion "0.5.1"
#define MyAppPublisher "AudioBench"
#define MyAppExeName "AudioBench.exe"

[Setup]
AppId={{8E1F2C3A-6B4D-4F2A-9C11-A1B2C3D4E5F6}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=AudioBench-{#MyAppVersion}-setup-x64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion=0.5.1.0
VersionInfoDescription={#MyAppName} installer
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
; 中文语言包随 packaging/languages 一起带，不依赖 Inno 安装目录里有没有
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\AudioBench\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "portable.flag,userdata\*,portable.flag\*"
Source: "licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\licenses"

[Code]
var
  KeepProjects: Boolean;

function IsSilentRun(): Boolean;
var
  Cmd: String;
begin
  { UninstallSilent 是 Inno 6 的内置函数；再用命令行兜底 }
  Result := UninstallSilent;
  if not Result then
  begin
    Cmd := UpperCase(ExpandConstant('{cmd}'));
    Result := (Pos('/SILENT', Cmd) > 0) or (Pos('/VERYSILENT', Cmd) > 0);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    // 静默卸载不弹框：默认保留用户数据（更安全）
    if IsSilentRun() then
      KeepProjects := True
    else if MsgBox('卸载时会一并删除用户工程、配置与缓存。' + #13#10 +
                   '是否保留它们（下次安装后仍可用）？' + #13#10#13#10 +
                   'Keep your projects, config and cache?',
                   mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO then
      KeepProjects := False
    else
      KeepProjects := True;
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    if not KeepProjects then
    begin
      DelTree(ExpandConstant('{userappdata}\AudioBench'), True, True, True);
      DelTree(ExpandConstant('{localappdata}\AudioBench'), True, True, True);
    end;
  end;
end;
