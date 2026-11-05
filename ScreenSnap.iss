; ScreenSnap 中文安装包脚本（Inno Setup 6）
; 编译：& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Qp ScreenSnap.iss
; 产物：release\ScreenSnap-Setup-1.0.0.exe
; 前置：先完成 PyInstaller 打包，确保 dist_qt\ScreenSnap-Qt\ScreenSnap-Qt.exe 存在

#define AppName "ScreenSnap"
#define AppVersion "1.0.0"
#define AppExe "ScreenSnap-Qt.exe"
#define SrcDir "dist_qt\ScreenSnap-Qt"

[Setup]
; 固定 AppId：升级安装时可原地覆盖，不产生重复的卸载条目
AppId={{6C1E4B2A-9D47-4F58-B3A6-7E0C58D91F42}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 安装包与卸载项显示信息
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
; 输出
OutputDir=release
OutputBaseFilename=ScreenSnap-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
; 压缩：LZMA2
Compression=lzma2/max
SolidCompression=yes
; 界面：安装向导只提供简体中文
ShowLanguageDialog=no
WizardStyle=modern
; 安装到 Program Files 需要提权
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; 卸载时保留用户配置与截图存档（%APPDATA%\ScreenSnap 与 图片目录），只清理程序目录
DisableDirPage=no
AllowNoIcons=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
; onedir 产物整体打包（含 _internal 下的 Qt 运行时与 assets 资源）
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 仅删除安装目录内由程序生成的空目录，不触碰用户数据目录
Type: dirifempty; Name: "{app}\_internal"

[Code]
// 安装前静默结束正在运行的实例：程序运行中会占用 exe 与 _internal 下的 Qt 库，
// 不先退出会导致 ReplaceFile/写文件失败。未运行时 taskkill 无副作用，静默安装也不会弹窗。
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM {#AppExe} /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;
