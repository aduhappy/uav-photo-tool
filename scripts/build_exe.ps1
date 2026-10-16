# 把 src/uav_photo_tool.py 打包成单文件 exe（PyInstaller --onefile --windowed）
# 用法：
#   pwsh -File scripts/build_exe.ps1 [-Python <python.exe>] [-Build <中间文件目录>] [-OutDir <exe 输出目录>]
# 也可以用环境变量 UAV_PYTHON / UAV_BUILD_DIR 指定前两项。
# 需要：Python 3.8+（带 tkinter）、PyInstaller（python -m pip install pyinstaller）、
#       界面库 CustomTkinter（python -m pip install -r requirements-gui.txt，版本钉死在该文件里，D-23）。

param(
    [string]$Python = $(if ($env:UAV_PYTHON) { $env:UAV_PYTHON } else { 'python' }),
    [string]$Build  = $(if ($env:UAV_BUILD_DIR) { $env:UAV_BUILD_DIR } else { Join-Path ([IO.Path]::GetTempPath()) 'uav_photo_tool_build' }),
    [string]$OutDir = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Src  = Join-Path $Root 'src\uav_photo_tool.py'
if (-not $OutDir) { $OutDir = Join-Path $Root '发布' }

# 版本号只从源码的 APP_VERSION 读，不在这里重复写
$m = Select-String -Path $Src -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' -Encoding utf8 | Select-Object -First 1
if (-not $m) { throw "在 $Src 里没找到 APP_VERSION" }
$Version = $m.Matches[0].Groups[1].Value
$Name    = "无人机照片工具_v$Version"
$Release = Join-Path $OutDir "$Name.exe"

# 界面库 CustomTkinter：开头先查已安装、版本和 requirements-gui.txt 钉的一致，否则打出来的 exe 界面打不开
$Req = Get-Content (Join-Path $Root 'requirements-gui.txt') -Encoding utf8 | Where-Object { $_ -match '^customtkinter==' } | Select-Object -First 1
if (-not $Req) { throw "requirements-gui.txt 里没找到 customtkinter==<版本>" }
$CtkWant = $Req.Split('==')[-1].Trim()

# 处理逻辑和命令行只用标准库，界面另带 CustomTkinter（D-23）；开发环境里常装着的大包排除掉，防止被误带进来。
# 注意：ssl / http / email / urllib 是「检查更新」要用的，不能排除。
$Excludes = @('numpy','pandas','scipy','matplotlib','PIL','IPython','jupyter','notebook','pytest',
              'setuptools','pkg_resources','distutils','lib2to3','pydoc_data','unittest','test',
              'sqlite3','multiprocessing','xmlrpc','urllib3','requests','cryptography','pywin32_system32','win32com')

# conda 环境没激活时 tk86t.dll / tcl86t.dll / libexpat.dll 等在 Library\bin 里找不到，会打出一个启动即崩的 exe
$PyExe = (Get-Command $Python).Source
$EnvDir = Split-Path -Parent $PyExe
if (Test-Path (Join-Path $EnvDir 'Library\bin')) {
    $env:PATH = (Join-Path $EnvDir 'Library\bin') + ';' + $EnvDir + ';' + $env:PATH
}

$CtkHave = & $PyExe -c "import customtkinter; print(customtkinter.__version__)" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $CtkHave) { throw "没装 CustomTkinter：先运行 $PyExe -m pip install -r requirements-gui.txt" }
if ($CtkHave.Trim() -ne $CtkWant) { throw "CustomTkinter 版本是 $CtkHave，requirements-gui.txt 钉的是 $CtkWant" }
Write-Host "CustomTkinter $CtkHave（与 requirements-gui.txt 一致）"

New-Item -ItemType Directory -Force (Join-Path $Build 'work'), (Join-Path $Build 'dist'), (Join-Path $Build 'spec'), $OutDir | Out-Null

$pyArgs = @('-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
          '--name', $Name,
          '--collect-data', 'customtkinter',   # 主题 json、图标、字体等资源文件，漏了 exe 一启动就报错
          '--workpath', (Join-Path $Build 'work'),
          '--distpath', (Join-Path $Build 'dist'),
          '--specpath', (Join-Path $Build 'spec'))
foreach ($x in $Excludes) { $pyArgs += @('--exclude-module', $x) }
$pyArgs += $Src

& $PyExe @pyArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败，退出码 $LASTEXITCODE" }

Copy-Item -Force (Join-Path $Build "dist\$Name.exe") $Release
$mb = [math]::Round((Get-Item $Release).Length / 1MB, 2)
Write-Host "完成：$Release （$mb MB）"
