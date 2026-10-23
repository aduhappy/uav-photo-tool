# 打包：PyInstaller 文件夹版（--onedir --windowed）+ Inno Setup 安装包 + 免安装 zip（D-34）
#
# 用法：
#   pwsh -File scripts/build_installer.ps1 -Python <python.exe> [-Build <中间文件目录>] [-OutDir <产物目录>]
# 也可以用环境变量 UAV_PYTHON / UAV_BUILD_DIR 指定前两项。
#
# 需要：
#   - Python 3.11（带 tkinter），PyInstaller（-m pip install pyinstaller）
#   - CustomTkinter 5.2.2（requirements-gui.txt）
#   - numpy / rasterio / shapely / pyproj（requirements.txt）—— 必须是**自带的 wheel**，
#     不能是 conda 装的那套（conda 的 numpy 依赖 MKL、DLL 在 Library\bin 里，带不走）。
#   - Inno Setup 6（本机 C:\Program Files (x86)\Inno Setup 6\ISCC.exe）
#
# 产物（都在 发布\ 里）：
#   无人机照片工具_v<版本>_安装包.exe     Inno Setup 编译出来的安装包
#   无人机照片工具_v<版本>_免安装.zip     同一个文件夹版，解压后双击 无人机照片工具.exe 即用

param(
    [string]$Python = $(if ($env:UAV_PYTHON) { $env:UAV_PYTHON } else { 'python' }),
    [string]$Build  = $(if ($env:UAV_BUILD_DIR) { $env:UAV_BUILD_DIR } else { Join-Path ([IO.Path]::GetTempPath()) 'uav_photo_tool_build' }),
    [string]$OutDir = '',
    [string]$Iscc   = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    [switch]$SkipZip
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Src  = Join-Path $Root 'src\uav_photo_tool.py'
if (-not $OutDir) { $OutDir = Join-Path $Root '发布' }

# ---- 版本号只从源码的 APP_VERSION 读 ----
$m = Select-String -Path $Src -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' -Encoding utf8 | Select-Object -First 1
if (-not $m) { throw "在 $Src 里没找到 APP_VERSION" }
$Version = $m.Matches[0].Groups[1].Value
$Name    = "无人机照片工具_v$Version"
$ExeName = "无人机照片工具.exe"
$Installer = Join-Path $OutDir "$Name`_安装包.exe"
$Zip       = Join-Path $OutDir "$Name`_免安装.zip"
Write-Host "版本 $Version"

$PyExe = (Get-Command $Python).Source
if (-not (Test-Path $PyExe)) { throw "找不到 Python：$Python" }
Write-Host "Python：$PyExe"

# ---- 核对已装版本和 requirements 钉的一致（不一致就停）----
function Get-Pinned([string]$file, [string]$name) {
    $line = Get-Content (Join-Path $Root $file) -Encoding utf8 | Where-Object { $_ -match "^$name==" } | Select-Object -First 1
    if (-not $line) { throw "$file 里没找到 $name==<版本>" }
    $line.Split('==')[-1].Trim()
}
function Get-Installed([string]$mod) {
    (& $PyExe -c "import importlib.metadata as m; print(m.version('$mod'))" 2>$null)
}
$Want = @{}
foreach ($f in @('requirements-gui.txt', 'requirements.txt')) {
    foreach ($line in (Get-Content (Join-Path $Root $f) -Encoding utf8)) {
        if ($line -match '^([A-Za-z0-9_.-]+)==(.+?)\s*$') { $Want[$Matches[1].ToLower()] = $Matches[2] }
    }
}
foreach ($k in ($Want.Keys | Sort-Object)) {
    $have = (Get-Installed $k)
    if ($LASTEXITCODE -ne 0 -or -not $have) { throw "没装 $k：先运行 $PyExe -m pip install -r requirements.txt -r requirements-gui.txt" }
    if ($have.Trim() -ne $Want[$k]) { throw "$k 版本是 $($have.Trim())，requirements 钉的是 $($Want[$k])" }
    Write-Host "  $k $($have.Trim())  （与 requirements 一致）"
}

# ---- 这些库必须是用 wheel 装的（带自己的 DLL），不是 conda 装的那套 ----
$probe = & $PyExe -c @"
import importlib.util, os, sys
for name in ('numpy', 'rasterio', 'shapely', 'pyproj'):
    spec = importlib.util.find_spec(name)
    p = list(spec.submodule_search_locations)[0]
    print(name, p)
"@
$probe | ForEach-Object { Write-Host "  $_" }
$condaLib = Join-Path (Split-Path -Parent (Split-Path -Parent $PyExe)) 'Library\bin'
if (Test-Path (Join-Path $condaLib 'mkl_rt.2.dll')) {
    Write-Warning "这个 Python 是 conda 环境：numpy 可能会链到 MKL，打出来的包在别人电脑上跑不起来。建议用独立的 Python 3.11 venv。"
}

# ---- 排除列表：处理逻辑不需要的大包（sqlite3 不再排除，T17 要用）----
$Excludes = @('pandas','scipy','matplotlib','IPython','jupyter','notebook','pytest',
              'setuptools','pkg_resources','distutils','lib2to3','pydoc_data','unittest','test',
              'multiprocessing','xmlrpc','urllib3','requests','cryptography','pywin32_system32','win32com')

New-Item -ItemType Directory -Force (Join-Path $Build 'work'), (Join-Path $Build 'dist'), (Join-Path $Build 'spec'), $OutDir | Out-Null

# ---- PyInstaller：文件夹版 ----
$pyArgs = @('-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
          '--name', $ExeName,
          '--collect-data', 'customtkinter',        # 主题 json、图标、字体等资源文件
          '--collect-all', 'rasterio',              # rasterio 自带 GDAL 的 DLL 和 gdal_data / proj_data
          '--collect-all', 'pyproj',                # pyproj 自带 proj_9.dll 和 proj_dir（含 proj.db）
          '--collect-all', 'shapely',               # shapely 自带 geos_c.dll
          '--collect-all', 'numpy',                 # numpy 自带的 OpenBLAS 等 DLL
          '--hidden-import', 'sqlite3',             # T17 读 3SM 要用
          '--workpath', (Join-Path $Build 'work'),
          '--distpath', (Join-Path $Build 'dist'),
          '--specpath', (Join-Path $Build 'spec'))
foreach ($x in $Excludes) { $pyArgs += @('--exclude-module', $x) }
$pyArgs += $Src

& $PyExe @pyArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败，退出码 $LASTEXITCODE" }

$Dist = Join-Path $Build "dist\$ExeName"
if (-not (Test-Path (Join-Path $Dist $ExeName))) { throw "没打出 $ExeName（找 $Dist）" }
Write-Host "文件夹版：$Dist"

# ---- 数据目录要真的在包里（GDAL_DATA / PROJ_DATA / proj.db）----
foreach ($need in @('_internal\rasterio\gdal_data', '_internal\rasterio\proj_data\proj.db',
                    '_internal\pyproj\proj_dir\share\proj\proj.db')) {
    if (-not (Test-Path (Join-Path $Dist $need))) { throw "包里少了 $need（GDAL/PROJ 数据没打进去）" }
}
Write-Host "GDAL/PROJ 数据都在包里"

# ---- Inno Setup：编译安装包 ----
if (-not (Test-Path $Iscc)) { throw "找不到 Inno Setup 编译器：$Iscc" }
$iss = Join-Path $Root 'installer\uav_photo_tool.iss'
$isl = Join-Path $Root 'installer\ChineseSimplified.isl'
if (-not (Test-Path $isl)) { throw "缺少中文语言文件 $isl" }
Remove-Item -Force -ErrorAction SilentlyContinue $Installer
# ISCC 的 /O 是"输出目录"，文件名由 .iss 里的 OutputBaseFilename 决定
& $Iscc "/DMyAppVersion=$Version" "/DMySourceDir=$Dist" "/DMyOutputDir=$OutDir" $iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 编译失败，退出码 $LASTEXITCODE" }
if (-not (Test-Path $Installer)) { throw "没生成安装包 $Installer（找找 $OutDir 里有没有别的名字）" }
$mb = [math]::Round((Get-Item $Installer).Length / 1MB, 2)
Write-Host "安装包：$Installer （$mb MB）"

# ---- 免安装 zip：UTF-8 文件名（ZIP flag bit 11） ----
if (-not $SkipZip) {
    Add-Type -AssemblyName System.IO.Compression
    $zipStream = $null
    $archive = $null
    try {
        Remove-Item -Force -ErrorAction SilentlyContinue $Zip
        $zipStream = [System.IO.File]::Open($Zip, [System.IO.FileMode]::CreateNew)
        $archive = [System.IO.Compression.ZipArchive]::new(
            $zipStream, [System.IO.Compression.ZipArchiveMode]::Create, $false,
            [System.Text.Encoding]::UTF8)
        $distPrefix = (Resolve-Path $Dist).Path.TrimEnd('\') + '\'
        foreach ($item in (Get-ChildItem -LiteralPath $Dist -Force -Recurse | Sort-Object FullName)) {
            $entryName = $item.FullName.Substring($distPrefix.Length).Replace('\', '/')
            if ($item.PSIsContainer) {
                $entryName += '/'
                [void]$archive.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
                continue
            }
            $entry = $archive.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
            $inputStream = [System.IO.File]::OpenRead($item.FullName)
            $outputStream = $entry.Open()
            try { $inputStream.CopyTo($outputStream) }
            finally { $outputStream.Dispose(); $inputStream.Dispose() }
        }
    }
    finally {
        if ($archive) { $archive.Dispose() }
        if ($zipStream) { $zipStream.Dispose() }
    }
    $zmb = [math]::Round((Get-Item $Zip).Length / 1MB, 2)
    Write-Host "免安装 zip：$Zip （$zmb MB，UTF-8 文件名）"
}

Write-Host "完成。"
