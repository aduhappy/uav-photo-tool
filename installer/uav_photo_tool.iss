; 无人机照片工具 安装包脚本（Inno Setup 6）
;
; 用法（一般由 scripts/build_installer.ps1 调用，不用手敲）：
;   ISCC.exe /DMyAppVersion=1.5 /DMySourceDir="<文件夹版目录>" /O<输出.exe> installer\uav_photo_tool.iss
;
; 约定（D-34）：
;   - 默认装到 %LOCALAPPDATA%\Programs\无人机照片工具\，不需要管理员权限（PrivilegesRequired=lowest），
;     用户在向导里可以改目录。
;   - 中文向导（ChineseSimplified.isl，和本脚本放在一起）。
;   - 开始菜单快捷方式；桌面快捷方式可选、默认勾。
;   - 同一个 AppId：装新版本时 Inno Setup 会先把旧版本卸掉再装。
;   - 卸载只删安装目录里的程序文件；%APPDATA%\无人机照片工具\ 下的 config.json 和用户数据一律不碰。

#ifndef MyAppVersion
  #error 请用 /DMyAppVersion=<版本> 指定版本号
#endif
#ifndef MySourceDir
  #error 请用 /DMySourceDir="<文件夹版目录>" 指定要打包的目录
#endif
#ifndef MyOutputDir
  #error 请用 /DMyOutputDir="<输出目录>" 指定安装包输出目录
#endif

#define MyAppName "无人机照片工具"
#define MyAppPublisher "uav-photo-tool"
#define MyAppExeName "无人机照片工具.exe"
#define MyAppId "{{8E3B7A41-6C25-4D9E-9B4C-2F71A5C8D310}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=uav-photo-tool
AppPublisherURL=https://github.com/aduhappy/uav-photo-tool
AppSupportURL=https://github.com/aduhappy/uav-photo-tool/issues
AppUpdatesURL=https://github.com/aduhappy/uav-photo-tool/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=无人机照片工具_v{#MyAppVersion}_安装包
OutputDir={#MyOutputDir}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 不用管理员权限：装到当前用户的 %LOCALAPPDATA%\Programs\
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
MinVersion=10.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; 文件夹版整个目录：主程序 + _internal（含 numpy/rasterio/GDAL/shapely/pyproj 和它们的数据）
Source: "{#MySourceDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MySourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; 中文语言文件
Source: "ChineseSimplified.isl"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清掉安装目录里可能残留的东西（程序文件本身 Inno Setup 会删）
Type: filesandordirs; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"
