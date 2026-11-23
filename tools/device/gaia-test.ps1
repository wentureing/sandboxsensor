<#
.SYNOPSIS
    自动化 Gaia（应用隐藏大师）内的探针测试流程。

.DESCRIPTION
    流程（用户确认的顺序）：
      1. 打开 Gaia
      2. 在应用列表里找到探针图标并点击
      3. 在新弹出的权限窗口点「授予」
      4. 等 guest 进程真正启动
      5. 抓 logcat + 提取探针报告

    设计要点：
      * 所有坐标都靠 uiautomator dump **动态查询**，不硬编码（Gaia 布局会变）
      * 每步都是「轮询等元素出现」而不是固定 sleep，抗慢设备
      * 失败会打印当前 UI 的可见文本，便于当场定位

.EXAMPLE
    .\tools\device\gaia-test.ps1
    # 完整流程

.EXAMPLE
    .\tools\device\gaia-test.ps1 -SkipLaunch
    # 只采集（探针已经在跑）
#>
param(
    [string]$Serial      = '10AFA8169Z0039G',
    [string]$GaiaPkg     = 'com.app.hider.master.pro.cn',
    [string]$GaiaAct     = 'com.android.launcher3.icon_origin',
    [string]$GuestLabel  = 'Sandbox Probe',
    [int]$ElementTimeout = 25,
    [int]$RunWaitSeconds = 12,
    [switch]$SkipLaunch,
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'
$w = Split-Path -Parent $PSScriptRoot
$reportDir = Join-Path $w 'probe-reports'
New-Item -ItemType Directory -Force $reportDir | Out-Null

# ---------------------------------------------------------------- helpers

function Adb([string[]]$Args) {
    $out = & adb -s $Serial @Args 2>&1
    return ($out | Out-String).Trim()
}

function DumpUi([string]$tag = 'dump') {
    $remote = "/sdcard/_ui_$tag.xml"
    Adb @('shell', "uiautomator dump $remote") | Out-Null
    # 中文路径下 adb pull 不稳定，用 cat 重定向
    $local = Join-Path $env:TEMP "_ui_$tag.xml"
    cmd /c "adb -s $Serial shell cat $remote > `"$local`" 2>nul"
    if (-not (Test-Path $local)) { return $null }
    return [xml](Get-Content $local -Raw -Encoding utf8)
}

<#
  在 UI 树里按「文本或 content-desc 包含某子串」找节点，返回其中心坐标。
  返回 $null 表示没找到。
#>
function FindNodeCenter([xml]$Ui, [string]$Needle) {
    if (-not $Ui) { return $null }
    foreach ($n in $Ui.SelectNodes('//node')) {
        $t  = $n.GetAttribute('text')
        $cd = $n.GetAttribute('content-desc')
        $label = if ($t) { $t } elseif ($cd) { $cd } else { '' }
        if ($label -and $label.Contains($Needle)) {
            $b = $n.GetAttribute('bounds')
            if ($b -match '\[(\d+),(\d+)\]\[(\d+),(\d+)\]') {
                $x1 = [int]$matches[1]; $y1 = [int]$matches[2]
                $x2 = [int]$matches[3]; $y2 = [int]$matches[4]
                return @{ X = [int](($x1 + $x2) / 2); Y = [int](($y1 + $y2) / 2); Bounds = $b }
            }
        }
    }
    return $null
}

function ShowUiLabels([xml]$Ui, [int]$max = 20) {
    if (-not $Ui) { Write-Host '    (UI dump 为空)' -ForegroundColor DarkYellow; return }
    $shown = 0
    foreach ($n in $Ui.SelectNodes('//node')) {
        $t = $n.GetAttribute('text'); $cd = $n.GetAttribute('content-desc')
        $label = if ($t) { $t } elseif ($cd) { $cd } else { '' }
        if ($label) {
            Write-Host ("      {0,-30} {1}" -f $label, $n.GetAttribute('bounds')) -ForegroundColor DarkGray
            if (++$shown -ge $max) { break }
        }
    }
}

<#
  轮询等待某个文本出现，返回中心坐标。
  $Candidates 任一命中即算成功。
#>
function WaitForAny([string[]]$Candidates, [int]$TimeoutSec, [string]$Tag) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $ui = DumpUi $Tag
        if ($ui) {
            foreach ($c in $Candidates) {
                $hit = FindNodeCenter $ui $c
                if ($hit) {
                    Write-Host "    ✓ 找到 '$c' @ ($($hit.X),$($hit.Y))" -ForegroundColor Green
                    return $hit
                }
            }
            $last = $ui
        }
        Start-Sleep -Milliseconds 600
    }
    Write-Host "    ✗ 超时未找到：$($Candidates -join ' / ')" -ForegroundColor Red
    ShowUiLabels $last
    return $null
}

function Tap([int]$X, [int]$Y) {
    Adb @('shell', "input tap $X $Y") | Out-Null
}

# ---------------------------------------------------------------- main

Write-Host "=== Gaia 自动化测试 ===" -ForegroundColor Cyan
Write-Host "  设备: $Serial"

if ($Reset) {
    Write-Host '  [Reset] force-stop 容器与探针（会改变现场，仅显式传 -Reset 时执行）' -ForegroundColor Yellow
    Adb @('shell', "am force-stop $GaiaPkg") | Out-Null
    Adb @('shell', 'am force-stop com.sandbox.probe') | Out-Null
    Start-Sleep -Seconds 2
}

if (-not $SkipLaunch) {
    # 1) 启动 Gaia
    Write-Host '[1/5] 启动 Gaia' -ForegroundColor Cyan
    $launcher = Adb @('shell', "cmd package resolve-activity --brief $GaiaPkg")
    $component = ($launcher -split "`n" | Where-Object { $_ -match "/" } | Select-Object -Last 1).Trim()
    if (-not $component) { $component = "$GaiaPkg/$GaiaAct" }
    Write-Host "  组件: $component"
    Adb @('shell', "am start -n $component") | Out-Null
    Start-Sleep -Seconds 4

    # 2) 找探针图标并点击
    Write-Host "[2/5] 在 Gaia 里找 '$GuestLabel'" -ForegroundColor Cyan
    $icon = WaitForAny @($GuestLabel) $ElementTimeout 'gaia_icon'
    if (-not $icon) {
        Write-Host '  ✗ 找不到探针图标，停止。' -ForegroundColor Red
        exit 2
    }
    Tap $icon.X $icon.Y
    Start-Sleep -Seconds 2

    # 3) 权限窗口点「授予」
    #    Gaia 首次启动 guest 会弹权限页；不同版本文案可能是 授予/允许/确定/同意
    Write-Host '[3/5] 等待权限窗口并点「授予」' -ForegroundColor Cyan
    $grant = WaitForAny @('授予', '允许', '同意', '确定', '始终允许') 10 'grant'
    if ($grant) {
        Tap $grant.X $grant.Y
        Start-Sleep -Seconds 2
        # 有些版本点完「授予」还会再弹一次确认
        $again = WaitForAny @('授予', '允许', '确定', '继续') 5 'grant2'
        if ($again) {
            Write-Host '    (第二次确认)' -ForegroundColor DarkYellow
            Tap $again.X $again.Y
            Start-Sleep -Seconds 2
        }
    } else {
        Write-Host '    (没有权限窗口，可能已授权过，继续)' -ForegroundColor DarkYellow
    }

    # 4) 等 guest 真正启动
    Write-Host '[4/5] 等 guest 进程' -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds($RunWaitSeconds + 8)
    $pidStr = ''
    while ((Get-Date) -lt $deadline) {
        $pidStr = (Adb @('shell', 'pidof com.sandbox.probe')).Trim()
        if ($pidStr) { break }
        Start-Sleep -Milliseconds 800
    }
    if ($pidStr) {
        Write-Host "  ✓ com.sandbox.probe pid=$pidStr" -ForegroundColor Green
    } else {
        Write-Host '  ✗ 探针进程未起来（可能被 Gaia 拦在权限页）' -ForegroundColor Red
        ShowUiLabels (DumpUi 'stuck')
    }
}

# 5) 采集
Write-Host "[5/5] 采集（等 $RunWaitSeconds 秒让 ProbeRunner 跑完）" -ForegroundColor Cyan
Start-Sleep -Seconds $RunWaitSeconds
$ts  = Get-Date -Format 'HHmmss'
$log = Join-Path $reportDir "gaia-$ts.txt"
cmd /c "adb -s $Serial logcat -d -b all -v threadtime > `"$log`" 2>nul"
$sizeMb = [math]::Round((Get-Item $log).Length / 1MB, 2)
$lines  = Get-Content $log -Encoding utf8
$blocks = ($lines | Select-String 'PROBE.*REPORT v1 part').Count
Write-Host "  日志: $log  ($sizeMb MB)  REPORT 分块=$blocks"

if ($blocks -gt 0) {
    Push-Location $w
    python tools/probe/extract-report.py $log 2>&1 | Select-Object -Last 3 | ForEach-Object { "  $_" }
    Pop-Location
} else {
    Write-Host '  没有 REPORT 分块：探针可能没跑到 ProbeRunner，或还没跑完' -ForegroundColor Yellow
    Write-Host '  相关日志：' -ForegroundColor DarkGray
    ($lines | Select-String 'PROBE|UiTestActivity|CmdReceiver' | Select-Object -Last 6) | ForEach-Object { "    " + $_.Line.Trim() }
}
