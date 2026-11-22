<#
.SYNOPSIS
  触发探针运行，并从 logcat 重组 base64 报告为 JSON。

.DESCRIPTION
  探针的安装由你手动完成（adb install 或在容器里导入），本脚本默认**只采集**。
  报告主通道是 logcat（tag=PROBE，分块 base64），因此**不需要 run-as**，
  包被卸载后作为 guest 导入容器运行时同样能采集。

.EXAMPLE
  # T1：手动安装后采集 golden 基线
  adb install -r -t .\probe-debug.apk
  .\tools\collect-probe.ps1 -Mode installed

.EXAMPLE
  # T2：手动在容器里导入并启动后采集
  .\tools\collect-probe.ps1 -Mode container

.EXAMPLE
  # 需要脚本代劳安装时
  .\tools\collect-probe.ps1 -Apk .\probe-debug.apk -Mode installed -Install
#>
[CmdletBinding()]
param(
    [string]$Apk,
    [ValidateSet('installed', 'container')][string]$Mode = 'installed',
    [string]$Out = 'probe-reports',
    [int]$TimeoutSec = 90,
    [switch]$Install,
    [string]$Serial,
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'

# ==================== 复位守卫 ====================
# 本脚本会清 logcat 并 force-stop 探针进程，可能破坏现场。
if (-not $Reset) {
    Write-Host "[复位守卫] 将执行：清 logcat + force-stop 探针。" -ForegroundColor Yellow
    Write-Host "[复位守卫] 已停止。确认要复位请加 -Reset 重新执行。" -ForegroundColor Yellow
    return
}
# ==================================================
$Pkg = 'com.sandbox.probe'
$Activity = "$Pkg/.ProbeActivity"

$adbArgs = @()
if ($Serial) { $adbArgs += @('-s', $Serial) }

# 用普通函数 + $args（不声明 param）：否则 -W / -d / -s 这类 adb 参数
# 会被 PowerShell 当成通用参数（-WarningAction 等）而报“参数名称歧义”。
function Invoke-Adb {
    $all = $adbArgs + $args
    & adb @all
}

New-Item -ItemType Directory -Force -Path $Out | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$reportPath = Join-Path $Out "report-$Mode-$stamp.json"

Write-Host "== 0. 设备信息 ==" -ForegroundColor Cyan
Invoke-Adb | Out-Null
$props = @(
    'ro.build.version.release', 'ro.build.version.sdk', 'ro.product.model',
    'ro.product.manufacturer', 'ro.product.cpu.abi', 'ro.build.fingerprint'
)
$devLines = foreach ($p in $props) {
    $v = (Invoke-Adb shell getprop $p) -join ''
    "$p=$v".Trim()
}
$pageSize = (Invoke-Adb shell getconf PAGESIZE) -join ''
$devLines = $devLines + "PAGESIZE=$pageSize"
$devLines | ForEach-Object { Write-Host "  $_" }
Set-Content -Path (Join-Path $Out "device-$Mode-$stamp.txt") -Value $devLines -Encoding utf8

if ($Install) {
    if (-not $Apk) { throw "指定 -Install 时必须提供 -Apk" }
    Write-Host "== 1. 安装探针（-Install）==" -ForegroundColor Cyan
    $res = Invoke-Adb install -r -t $Apk 2>&1
    Write-Host ($res -join "`n")
    if (($res -join "`n") -notmatch 'Success') { throw "安装失败" }
} else {
    Write-Host "== 1. 跳过安装（手动安装模式）==" -ForegroundColor DarkGray
}

Write-Host "== 2. 清空 logcat 并启动 ==" -ForegroundColor Cyan
Invoke-Adb logcat -c | Out-Null
Invoke-Adb shell am force-stop $Pkg | Out-Null
Invoke-Adb shell am start -W -n $Activity | Out-Null

Write-Host "== 3. 等待 REPORT 结束标记（最长 $TimeoutSec 秒）==" -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$chunks = @{}
$total = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    $lines = Invoke-Adb logcat -d -s PROBE:I 2>$null
    $chunks = @{}
    $total = $null
    foreach ($line in $lines) {
        if ($line -match 'REPORT v1 part (\d+)/(\d+) (\S+)') {
            $chunks[[int]$Matches[1]] = $Matches[3]
            $total = [int]$Matches[2]
        }
    }
    if ($total -and $chunks.Count -eq $total) { break }
}

if (-not $total -or $chunks.Count -ne $total) {
    Write-Warning "未集齐分块（收到 $($chunks.Count)/$($total ?? 0)）。原始日志已保存。"
    Invoke-Adb logcat -d -s PROBE:I | Set-Content -Path (Join-Path $Out "logcat-$Mode-$stamp.txt") -Encoding utf8
    throw "报告不完整"
}

Write-Host "  收到 $total 个分块，重组中…" -ForegroundColor Green
$b64 = ($chunks.Keys | Sort-Object | ForEach-Object { $chunks[$_] }) -join ''
$bytes = [Convert]::FromBase64String($b64)
[IO.File]::WriteAllBytes((Resolve-Path -LiteralPath $Out).Path + "\" + (Split-Path $reportPath -Leaf), $bytes)

Invoke-Adb logcat -d -s PROBE:I | Set-Content -Path (Join-Path $Out "logcat-$Mode-$stamp.txt") -Encoding utf8

$json = Get-Content -Raw -Encoding utf8 $reportPath | ConvertFrom-Json
Write-Host "== 4. 摘要 ==" -ForegroundColor Cyan
Write-Host ("  mode      = {0}" -f $json.mode)
Write-Host ("  package   = {0}" -f $json.packageName)
Write-Host ("  timeline  : {0}" -f (($json.timeline | ForEach-Object { $_.phase }) -join ' -> '))
$okCount = ($json.entries | Where-Object { $_.status -eq 'ok' }).Count
$failCount = ($json.entries | Where-Object { $_.status -ne 'ok' }).Count
Write-Host ("  entries   = {0} ok / {1} fail" -f $okCount, $failCount)
Write-Host ("  报告      = {0}" -f (Resolve-Path $reportPath))
