# 批量「控制点·迁移」：扫描一个上级目录下的每一期照片，按目录名自动选参考期，可以筛选、手动勾选，然后一期接一期地跑，最后出汇总表。
# 需要：无人机照片工具 v0.5 以上（gcp-transfer 命令）、Metashape Pro。Windows PowerShell 5.1 或 PowerShell 7 都能跑。
#
# 用法（在 PowerShell 里）：
#   .\batch_gcp_transfer.ps1 -Tool <工具 exe> -Source <照片上级目录> -Out <输出目录> `
#       [-RefTerrace <梯田参考期.psx>] [-RefVeg <植被参考期.psx>] [选择] [其他]
#
# 选择要跑哪些期（可以组合，先按 -Include 筛，再按 -Pick / -Select 选）：
#   -Select          扫描后弹出表格窗口：顶上的筛选框输入「梯田」等即可过滤；Ctrl/Shift 多选，点「确定」只跑选中的。
#                    （Windows PowerShell 5.1 和 Windows 上的 PowerShell 7 都自带这个窗口；加 -NoGui 或没有窗口时，改为在命令行里输入序号。）
#   -Include "*梯田*"  只要名字匹配的期（通配符，多个用逗号隔开，如 "*梯田*,*植被降雨前*"）。
#   -Pick "1,3,5-9"  按扫描列表的序号选（先用 -Plan 看序号）。
#   只给 -RefTerrace、不给 -RefVeg，也就等于只跑梯田。
# 其他：
#   -Plan            只列出扫描结果（序号、类型、照片数、参考期、是否已有结果），不实际运行。
#   -Skip a,b        不处理的期次名（比如参考期本身）。
#   -ExcludeTerrace / -ExcludeVeg：给梯田 / 植被各期统一加 --exclude（例：-ExcludeTerrace 4）。被排除的点不参与平差、改作检查点，误差照报。默认不排除。
#
# 规则：
#   - Source 下每个直接含 JPG 的子目录算一期；名字带「梯田」用 -RefTerrace，带「植被」用 -RefVeg，都不带就不能跑。
#   - 名字以 _去定位 / _空三 / _控制点 结尾的目录不算新一期。
#   - Out 下已经有这一期的「控制点报告.txt」就跳过（中途停了重跑会接着做）。
#   - 想重跑某一期（比如判了「需人工核对」想再试一次）：删掉 Out 下这一期的 <名>_去定位_控制点 目录再运行；已去定位的照片会直接复用。
#   - 每期的去定位照片和结果都写在 Out 下，Source 只读。
#   - 每期开跑前如果机器上已经有 Metashape 在跑，就等它结束。
#   - 汇总表：Out\批量迁移汇总_<时间>.csv（UTF-8 带 BOM，Excel 直接打开），只含这次选中的期。

param(
    [Parameter(Mandatory = $true)][string]$Tool,
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$RefTerrace = '',
    [string]$RefVeg = '',
    [string[]]$Skip = @(),
    [string[]]$Include = @(),
    [string]$Pick = '',
    [switch]$Select,
    [switch]$NoGui,       # 配合 -Select：不弹表格窗口，改在命令行里输入序号（远程/无桌面时用）
    [string]$ExcludeTerrace = '',   # 梯田各期都排除的控制点，如 '4'（改作检查点，照报误差）
    [string]$ExcludeVeg = '',       # 植被各期都排除的控制点
    [switch]$Plan
)

$ErrorActionPreference = 'Stop'
foreach ($p in @($Tool, $Source)) { if (-not (Test-Path $p)) { throw "找不到：$p" } }
foreach ($p in @($RefTerrace, $RefVeg)) { if ($p -and -not (Test-Path $p)) { throw "找不到参考期工程：$p" } }
if (-not $RefTerrace -and -not $RefVeg) { throw "-RefTerrace 和 -RefVeg 至少给一个" }
New-Item -ItemType Directory -Force $Out | Out-Null

function Split-List($v) {   # 用 -File 调用时逗号不会自动拆成数组，这里统一拆
    @($v | ForEach-Object { $_ -split '[,，]' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
$Skip = Split-List $Skip
$Include = Split-List $Include

function Parse-Pick([string]$text, [int]$max) {
    $set = New-Object System.Collections.Generic.HashSet[int]
    foreach ($part in (Split-List $text)) {
        if ($part -match '^(\d+)\s*-\s*(\d+)$') { foreach ($k in [int]$Matches[1]..[int]$Matches[2]) { [void]$set.Add($k) } }
        elseif ($part -match '^\d+$') { [void]$set.Add([int]$part) }
        else { throw "看不懂的序号：$part（写成 1,3,5-9 这样）" }
    }
    foreach ($k in $set) { if ($k -lt 1 -or $k -gt $max) { throw "序号 $k 超出范围 1～$max" } }
    $set
}

$exitText = @{ 0 = '自动通过'; 1 = '失败'; 2 = '拒绝'; 3 = '已取消'; 4 = '需人工核对' }
$csvEnc = if ($PSVersionTable.PSVersion.Major -ge 6) { 'utf8BOM' } else { 'UTF8' }   # 两个版本都写带 BOM 的 UTF-8

function Find-Report([string]$outDir, [string]$name) {
    # 工具的输出目录：<名>_控制点 或 <名>_去定位_控制点（照片带定位、先去定位时）
    foreach ($d in @("$name`_控制点", "$name`_去定位_控制点")) {
        $r = Join-Path (Join-Path $outDir $d) '控制点报告.txt'
        if (Test-Path $r) { return $r }
    }
    return $null
}

function Read-Report([string]$report) {
    $t = Get-Content $report -Encoding UTF8
    $verdict = ($t | Select-String '^判定：(.+)$' | Select-Object -First 1)
    $rms = ($t | Select-String '^RMS3D：(.+)$' | Select-Object -First 1)
    $aligned = ($t | Select-String '^对齐：(.+)$' | Select-Object -First 1)
    [pscustomobject]@{
        判定 = if ($verdict) { $verdict.Matches[0].Groups[1].Value } else { '' }
        RMS3D = if ($rms) { $rms.Matches[0].Groups[1].Value } else { '' }
        对齐 = if ($aligned) { $aligned.Matches[0].Groups[1].Value } else { '' }
    }
}

# ---------------- 第 1 步：扫描
$dirs = Get-ChildItem $Source -Directory | Where-Object { $_.Name -notmatch '_(去定位|空三|控制点)$' } | Sort-Object Name
$scan = New-Object System.Collections.Generic.List[object]
$n = 0
foreach ($d in $dirs) {
    $jpg = @(Get-ChildItem $d.FullName -File | Where-Object { $_.Extension -match '^\.(jpe?g)$' }).Count
    if ($jpg -eq 0) { continue }
    $n++
    $name = $d.Name
    $type = if ($name -like '*梯田*') { '梯田' } elseif ($name -like '*植被*') { '植被' } else { '未知' }
    $ref = ''; $excl = ''; $why = ''
    if ($Skip -contains $name) { $why = '在 -Skip 里' }
    elseif ($type -eq '梯田') { if ($RefTerrace) { $ref = $RefTerrace; $excl = $ExcludeTerrace } else { $why = '是梯田，但没给 -RefTerrace' } }
    elseif ($type -eq '植被') { if ($RefVeg) { $ref = $RefVeg; $excl = $ExcludeVeg } else { $why = '是植被，但没给 -RefVeg' } }
    else { $why = '目录名里没有「梯田」或「植被」' }
    $done = Find-Report $Out $name
    if (-not $why -and $done) { $why = '已有结果' }
    $scan.Add([pscustomobject][ordered]@{
        序号 = $n; 期次 = $name; 类型 = $type; 照片数 = $jpg
        参考期 = $(if ($ref) { Split-Path $ref -Leaf } else { '' }); 排除点 = $excl
        状态 = $(if ($why) { "跳过：$why" } else { '待运行' })
        _dir = $d.FullName; _ref = $ref; _done = $done
    })
}
"共找到 $($scan.Count) 期照片目录。"

# ---------------- 第 2 步：筛选和选择
$chosen = $scan.ToArray()   # PS 5.1 里 @(List) 会报 Argument types do not match
if ($Include.Count) {
    $chosen = @($chosen | Where-Object { $nm = $_.期次; ($Include | Where-Object { $nm -like $_ }).Count -gt 0 })
    "按 -Include 筛选后剩 $($chosen.Count) 期。"
}
if ($Pick) {
    $set = Parse-Pick $Pick $scan.Count
    $chosen = @($chosen | Where-Object { $set.Contains([int]$_.序号) })
    "按 -Pick 选中 $($chosen.Count) 期。"
}
if ($Select) {
    $view = $chosen | Select-Object 序号, 期次, 类型, 照片数, 参考期, 排除点, 状态
    $ogv = if ($NoGui) { $null } else { Get-Command Out-GridView -ErrorAction SilentlyContinue }
    if ($ogv) {
        $picked = @($view | Out-GridView -Title "选要跑的期次（顶上筛选框可过滤；Ctrl/Shift 多选；点「确定」）" -PassThru)
        $ids = @($picked | ForEach-Object { [int]$_.序号 })
    }
    else {
        $view | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
        $ans = Read-Host "输入要跑的序号（如 1,3,5-9；直接回车＝列出的全部）"
        $ids = if ($ans.Trim()) { @(Parse-Pick $ans $scan.Count) } else { @($view | ForEach-Object { [int]$_.序号 }) }
    }
    $chosen = @($chosen | Where-Object { $ids -contains [int]$_.序号 })
    "手动选中 $($chosen.Count) 期。"
}
if ($chosen.Count -eq 0) { "没有选中任何期次，结束。"; return }

# ---------------- 第 3 步：列计划或逐期运行
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$summary = Join-Path $Out "批量迁移汇总_$stamp.csv"
$rows = New-Object System.Collections.Generic.List[object]
$k = 0
foreach ($c in $chosen) {
    $k++
    $tag = "[$k/$($chosen.Count) · 序号 $($c.序号)] $($c.期次)"
    $row = [ordered]@{ 序号 = $c.序号; 期次 = $c.期次; 类型 = $c.类型; 照片数 = $c.照片数; 参考期 = $c.参考期; 排除点 = $c.排除点; 状态 = ''; 退出码 = ''; 判定 = ''; RMS3D = ''; 对齐 = ''; 耗时分钟 = ''; 报告 = '' }
    if ($c.状态 -ne '待运行') {
        $row.状态 = $c.状态
        if ($c._done) { $r = Read-Report $c._done; $row.判定 = $r.判定; $row.RMS3D = $r.RMS3D; $row.对齐 = $r.对齐; $row.报告 = $c._done }
        "$tag → $($c.状态)"
    }
    elseif ($Plan) {
        $row.状态 = '计划运行'
        "$tag（$($c.类型)，$($c.照片数) 张）→ 会用参考期 $($c.参考期)$(if ($c.排除点) { "，排除点 $($c.排除点)" })"
    }
    else {
        while (Get-Process metashape -ErrorAction SilentlyContinue) { "  机器上已有 Metashape 在跑，等 1 分钟…"; Start-Sleep 60 }
        "$tag → 开始（参考期 $($c.参考期)） $(Get-Date -Format 'HH:mm')"
        $t0 = Get-Date
        $logFile = Join-Path $Out "$($c.期次)_运行日志.txt"
        # 上次已经去定位过（输出目录里有 <名>_去定位），就直接用它，避免重跑时工具因去定位目录非空而拒绝
        $photoDir = $c._dir
        $stripped = Join-Path $Out "$($c.期次)_去定位"
        if ((Test-Path $stripped) -and (Get-ChildItem $stripped -File | Where-Object { $_.Extension -match '^\.(jpe?g)$' } | Select-Object -First 1)) {
            $photoDir = $stripped
            "  用已去定位的照片：$stripped"
        }
        $toolArgs = @('gcp-transfer', "`"$photoDir`"", '--ref', "`"$($c._ref)`"", '--out', "`"$Out`"", '--log', "`"$logFile`"")
        if ($c.排除点) { $toolArgs += @('--exclude', $c.排除点) }
        $p = Start-Process $Tool -ArgumentList $toolArgs -PassThru -Wait
        $row.耗时分钟 = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
        $row.退出码 = $p.ExitCode
        $row.状态 = if ($exitText.ContainsKey($p.ExitCode)) { $exitText[$p.ExitCode] } else { "未知退出码 $($p.ExitCode)" }
        $rep = Find-Report $Out $c.期次
        if ($rep) { $r = Read-Report $rep; $row.判定 = $r.判定; $row.RMS3D = $r.RMS3D; $row.对齐 = $r.对齐; $row.报告 = $rep }
        "  → $($row.状态)  $($row.RMS3D)  用时 $($row.耗时分钟) 分钟"
    }
    $rows.Add([pscustomobject]$row)
    $rows | Export-Csv $summary -NoTypeInformation -Encoding $csvEnc   # 每期写一次，中途停了也有汇总
}
""
"汇总表：$summary"
$rows | Group-Object 状态 | ForEach-Object { "  $($_.Name)：$($_.Count) 期" }
