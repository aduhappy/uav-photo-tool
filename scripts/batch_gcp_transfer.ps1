# 批量「控制点·迁移」：对一个上级目录下的每一期照片，按目录名自动选参考期，一期接一期地跑，最后出汇总表。
# 需要：无人机照片工具 v0.5 以上（gcp-transfer 命令）、Metashape Pro。Windows PowerShell 5.1 或 PowerShell 7 都能跑。
#
# 用法（在 PowerShell 里）：
#   .\batch_gcp_transfer.ps1 -Tool <工具 exe> -Source <照片上级目录> -Out <输出目录> `
#       -RefTerrace <梯田参考期.psx> -RefVeg <植被参考期.psx> [-Skip 26.08.01梯田,...] [-Plan]
#   -Plan：只列出每一期会用哪个参考期、会不会跳过，不实际运行。
#   -ExcludeTerrace / -ExcludeVeg：给梯田 / 植被各期统一加 --exclude（例：-ExcludeTerrace 4）。被排除的点不参与平差、改作检查点，误差照报。默认不排除。
#
# 规则：
#   - Source 下每个直接含 JPG 的子目录算一期；名字带「梯田」用 -RefTerrace，带「植被」用 -RefVeg，都不带就跳过（汇总里写原因）。
#   - 名字以 _去定位 / _空三 / _控制点 结尾的目录不算新一期，跳过。
#   - Out 下已经有这一期的「控制点报告.txt」就跳过（中途停了重跑会接着做）。
#   - 想重跑某一期（比如判了「需人工核对」想再试一次）：删掉 Out 下这一期的 <名>_去定位_控制点 目录再运行；已去定位的照片会直接复用。
#   - 每期的去定位照片和结果都写在 Out 下，Source 只读。
#   - 每期开跑前如果机器上已经有 Metashape 在跑，就等它结束。
#   - 汇总表：Out\批量迁移汇总_<时间>.csv（UTF-8 带 BOM，Excel 直接打开）。

param(
    [Parameter(Mandatory = $true)][string]$Tool,
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$RefTerrace = '',
    [string]$RefVeg = '',
    [string[]]$Skip = @(),
    [string]$ExcludeTerrace = '',   # 梯田各期都排除的控制点，如 '4'（改作检查点，照报误差）
    [string]$ExcludeVeg = '',       # 植被各期都排除的控制点
    [switch]$Plan
)

$ErrorActionPreference = 'Stop'
foreach ($p in @($Tool, $Source)) { if (-not (Test-Path $p)) { throw "找不到：$p" } }
foreach ($p in @($RefTerrace, $RefVeg)) { if ($p -and -not (Test-Path $p)) { throw "找不到参考期工程：$p" } }
if (-not $RefTerrace -and -not $RefVeg) { throw "-RefTerrace 和 -RefVeg 至少给一个" }
New-Item -ItemType Directory -Force $Out | Out-Null
$Skip = @($Skip | ForEach-Object { $_ -split '[,，]' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })   # 用 -File 调用时逗号不会自动拆成数组

$exitText = @{ 0 = '自动通过'; 1 = '失败'; 2 = '拒绝'; 3 = '已取消'; 4 = '需人工核对' }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$summary = Join-Path $Out "批量迁移汇总_$stamp.csv"
$rows = New-Object System.Collections.Generic.List[object]
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

$periods = Get-ChildItem $Source -Directory | Where-Object {
    $_.Name -notmatch '_(去定位|空三|控制点)$' -and (Get-ChildItem $_.FullName -File | Where-Object { $_.Extension -match '^\.(jpe?g)$' } | Select-Object -First 1)
} | Sort-Object Name

"共找到 $($periods.Count) 期照片目录。"
$i = 0
foreach ($d in $periods) {
    $i++
    $name = $d.Name
    $ref = ''
    $excl = ''
    $why = ''
    if ($Skip -contains $name) { $why = '在 -Skip 里' }
    elseif ($name -like '*梯田*') { if ($RefTerrace) { $ref = $RefTerrace; $excl = $ExcludeTerrace } else { $why = '是梯田，但没给 -RefTerrace' } }
    elseif ($name -like '*植被*') { if ($RefVeg) { $ref = $RefVeg; $excl = $ExcludeVeg } else { $why = '是植被，但没给 -RefVeg' } }
    else { $why = '目录名里没有「梯田」或「植被」' }
    $done = Find-Report $Out $name
    if (-not $why -and $done) { $why = '已有结果' }

    $row = [ordered]@{ 序号 = $i; 期次 = $name; 参考期 = $(if ($ref) { Split-Path $ref -Leaf } else { '' }); 排除点 = $excl; 状态 = ''; 退出码 = ''; 判定 = ''; RMS3D = ''; 对齐 = ''; 耗时分钟 = ''; 报告 = '' }
    if ($why) {
        $row.状态 = "跳过：$why"
        if ($done) { $r = Read-Report $done; $row.判定 = $r.判定; $row.RMS3D = $r.RMS3D; $row.对齐 = $r.对齐; $row.报告 = $done }
        "[$i/$($periods.Count)] $name → 跳过（$why）"
    }
    elseif ($Plan) {
        $row.状态 = '计划运行'
        "[$i/$($periods.Count)] $name → 会用参考期 $(Split-Path $ref -Leaf)$(if ($excl) { "，排除点 $excl" })"
    }
    else {
        while (Get-Process metashape -ErrorAction SilentlyContinue) { "  机器上已有 Metashape 在跑，等 1 分钟…"; Start-Sleep 60 }
        "[$i/$($periods.Count)] $name → 开始（参考期 $(Split-Path $ref -Leaf)） $(Get-Date -Format 'HH:mm')"
        $t0 = Get-Date
        $logFile = Join-Path $Out "$name`_运行日志.txt"
        # 上次已经去定位过（输出目录里有 <名>_去定位），就直接用它，避免重跑时工具因去定位目录非空而拒绝
        $photoDir = $d.FullName
        $stripped = Join-Path $Out "$name`_去定位"
        if ((Test-Path $stripped) -and (Get-ChildItem $stripped -File | Where-Object { $_.Extension -match '^\.(jpe?g)$' } | Select-Object -First 1)) {
            $photoDir = $stripped
            "  用已去定位的照片：$stripped"
        }
        $p = Start-Process $Tool -ArgumentList ($(@('gcp-transfer', "`"$photoDir`"", '--ref', "`"$ref`"", '--out', "`"$Out`"", '--log', "`"$logFile`"")) + $(if ($excl) { @('--exclude', $excl) } else { @() })) -PassThru -Wait
        $row.耗时分钟 = [math]::Round(((Get-Date) - $t0).TotalMinutes, 1)
        $row.退出码 = $p.ExitCode
        $row.状态 = if ($exitText.ContainsKey($p.ExitCode)) { $exitText[$p.ExitCode] } else { "未知退出码 $($p.ExitCode)" }
        $rep = Find-Report $Out $name
        if ($rep) { $r = Read-Report $rep; $row.判定 = $r.判定; $row.RMS3D = $r.RMS3D; $row.对齐 = $r.对齐; $row.报告 = $rep }
        "  → $($row.状态)  $($row.RMS3D)  用时 $($row.耗时分钟) 分钟"
    }
    $rows.Add([pscustomobject]$row)
    $rows | Export-Csv $summary -NoTypeInformation -Encoding $csvEnc   # 每期写一次，中途停了也有汇总
}
""
"汇总表：$summary"
$rows | Group-Object 状态 | ForEach-Object { "  $($_.Name)：$($_.Count) 期" }
