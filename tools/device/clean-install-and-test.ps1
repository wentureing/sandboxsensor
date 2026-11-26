<#
.SYNOPSIS
  干净安装容器 APK 并跑冒烟三连（SELF_CHECK / 探针 round / via 启动），全部用设备端等待。

.DESCRIPTION
  "干净安装"= 卸载再装：**会清掉沙箱数据与 appops 授权**（这正是它的目的）。
  装完补两件事：重授 `MANAGE_EXTERNAL_STORAGE`、重新导入 guest APK。

  设计约束（2026-11-26 用户要求）：
  1. **不做截图**（不需要）。
  2. **等待放在设备端**：一次 `adb shell` 里用 `while`/`sleep` 轮询到"任务完成信号"再返回，
     PC 端不再每秒调一次 adb（原来一轮 40+ 次调用）。
  3. **判定只读文件**：干净安装会换容器 uid，vivo 会吞该 uid 进程的 I 级日志
     （实测 `logcat --pid=<容器pid>` 全空，而 `files/checks.log` 一直在写）。
  4. 容器启动后**固定等 10s**（早期窗口：PM 包列表未就绪，J19/J20 会 NameNotFound）。

  三个"完成信号"：
    - 导入完成 = sandbox 里出现 `<pkg>_<ts>` 实例目录
    - 自检完成 = `files/checks.log` 新增了 `hookcheck` 行（对比行数基线）
    - guest 就绪 = 最新 `breadcrumb-*.log` 里出现 `onActivityResumed`

.EXAMPLE
  .\tools\device\clean-install-and-test.ps1 -Apk dist\container-125.apk
  .\tools\device\clean-install-and-test.ps1 -Apk dist\x.apk -SkipImport
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Apk,
    [string]$Serial = '10AFA8169Z0039G',
    [string]$Out = 'probe-reports/clean-install',
    [string]$ViaApk = '/data/local/tmp/via.apk',
    [string]$ProbeApk = '/data/local/tmp/probe-new.apk',
    [switch]$SkipImport
)

$ErrorActionPreference = 'Stop'
$HostPkg = 'com.nextvm.app'
$DataDir = 'files/virtual/data'

function Sh([string]$cmd) {
    $r = & adb -s $Serial shell $cmd 2>&1
    return (($r | Out-String) -replace "`r", '').Trim()
}
function Cmd([string]$action, [string]$extra = '') {
    Sh "am broadcast -n $HostPkg/.CmdReceiver -a com.nextvm.cmd.$action $extra" | Out-Null
}
function Info([string]$m) { Write-Host "  $m" -ForegroundColor Gray }
function Ok([string]$m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Bad([string]$m) { Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Die([string]$m) { throw "[clean-install] $m" }

# 设备端等待（一次 adb 调用）：`until 条件; do sleep 0.2; done` + `timeout` 兜底。
# 两条硬约束（都实测踩过）：
#   1. 脚本里**不要用 `$(( ))` / `$( )`** —— PowerShell 会先解析，报 `unmatched 'while'`；
#   2. 脚本里**不要用单引号** —— 外层是 `sh -c '...'`，内层单引号会把引号截断
#      （`grep -m1 'bind complete'` 会变成 `grep -m1 bind complete`，第二个词被当文件名）。
#      需要匹配空格时用正则点号，如 `grep -m1 bind.complete`。
function ShWait([string]$deviceScript) {
    return Sh "run-as $HostPkg timeout 25 sh -c '$deviceScript'"
}
# 等 sandbox 出现某 pkg 的实例目录
function Wait-Instance([string]$pkg) {
    return ShWait "until ls $DataDir 2>/dev/null | grep -q $pkg; do sleep 0.2; done; ls $DataDir | grep $pkg | tail -1"
}

New-Item -ItemType Directory -Force -Path $Out | Out-Null
$outDir = (Resolve-Path $Out).Path

# ---------------------------------------------------------------- 0 前置
if (-not (Test-Path $Apk)) { Die "找不到 APK：$Apk" }
Info ("device={0} apk={1}" -f (Sh "getprop ro.product.model"), (Split-Path $Apk -Leaf))

# ---------------------------------------------------------------- 1 干净安装（1 次调用）
if (Sh "pm list packages $HostPkg") {
    Info "卸载旧包（清沙箱数据与 appops）"
    & adb -s $Serial uninstall $HostPkg | Out-Null
}
$r = & adb -s $Serial install -t $Apk 2>&1 | Out-String
if ($r -notmatch 'Success') { Die "安装失败：$r" }
Ok ("安装成功：{0}" -f ((Sh "dumpsys package $HostPkg | grep -E 'versionName' | head -1") -replace '\s+', ' '))

# ---------------------------------------------------------------- 2 授权 + 启动 + 等 10s（2 次调用）
Sh "appops set $HostPkg MANAGE_EXTERNAL_STORAGE allow"
$op = Sh "appops get $HostPkg MANAGE_EXTERNAL_STORAGE"
if ($op -match 'allow') { Ok "appops allow" } else { Bad "appops 未生效：$op" }

Sh "am start -n $HostPkg/.MainActivity" | Out-Null
Info "容器启动，等 10s（早期窗口）"
Start-Sleep -Seconds 10
# 就绪确认与等待合并成一次设备端调用
$ready = ShWait "ls files/ > /dev/null 2>&1 && echo READY || echo NOT-READY"
if ($ready -eq 'READY') { Ok "容器已就绪（10s + 私有目录可读）" } else { Bad "app 私有目录读不到" }

# ---------------------------------------------------------------- 3 导入 guest（每条 1 次 install + 1 次设备端等待）
if (-not $SkipImport) {
    foreach ($pair in @(@('via', 'mark.via', $ViaApk), @('probe', 'com.sandbox.probe', $ProbeApk))) {
        $name = $pair[0]; $pkg = $pair[1]; $path = $pair[2]
        if (-not (Sh "ls $path")) { Bad "${name}: 设备上没有 ${path}，跳过"; continue }
        Cmd 'INSTALL' "--es path $path"
        $inst = Wait-Instance $pkg
        if ($inst -eq 'TIMEOUT' -or -not $inst) { Bad "$name 导入失败（20s 内无实例目录）" }
        else { Ok "$name 导入成功（$inst）" }
    }
}

# ---------------------------------------------------------------- 4 SELF_CHECK（广播 + 一次设备端等待）
# 干净安装后 checks.log 是新的，所以「出现 hookcheck 行」即本次自检完成。
Cmd 'SELF_CHECK'
$self = ShWait "until grep -q hookcheck files/checks.log 2>/dev/null; do sleep 0.2; done; grep hookcheck files/checks.log | tail -1"
if ($self -match 'ok=(\d+) fail=(\d+)') {
    if ([int]$Matches[2] -eq 0) { Ok "SELF_CHECK ok=$($Matches[1]) fail=0（files/checks.log）" }
    else { Bad "SELF_CHECK 有失败：$self" }
} else { Bad "SELF_CHECK 无结果：$self" }

# ---------------------------------------------------------------- 5 探针 round（1 广播 + 1 设备端等待）
Sh "setprop debug.probe.suite round" | Out-Null
Cmd 'LAUNCH_ACTIVITY' '--es pkg com.sandbox.probe --es activity com.sandbox.probe.ProbeActivity'
$probePath = ShWait "until ls $DataDir/com.sandbox.probe_*/files/probe-report.json >/dev/null 2>&1; do sleep 0.2; done; ls -t $DataDir/com.sandbox.probe_*/files/probe-report.json | head -1"
Sh "setprop debug.probe.suite ''" | Out-Null
if (-not $probePath) {
    Bad "探针报告未出现"
} else {
    $probeOut = Join-Path $outDir 'probe-report.json'
    & adb -s $Serial shell "run-as $HostPkg cat $probePath" 2>$null | Set-Content $probeOut -Encoding utf8NoBOM
    $j = Get-Content $probeOut -Raw | ConvertFrom-Json
    $stat = ($j.entries | Group-Object status | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ' '
    $fails = ($j.entries | Where-Object { $_.status -eq 'fail' } | ForEach-Object { $_.id }) -join ','
    Ok ("探针 entries={0} [{1}] fail={2}" -f $j.entries.Count, $stat, $fails)
}

# ---------------------------------------------------------------- 6 via 启动（1 广播 + 1 设备端等待）
# 判据只看 golden 三要素：bind start / bind complete / onActivityResumed（实测 0.2–1s）
Cmd 'LAUNCH' '--es pkg mark.via'
$via = ShWait "until ls $DataDir/mark.via_*/files/breadcrumb-*.log >/dev/null 2>&1 && grep -q onActivityResumed $DataDir/mark.via_*/files/breadcrumb-*.log 2>/dev/null; do sleep 0.2; done; grep -h -m1 bind.complete $DataDir/mark.via_*/files/breadcrumb-*.log"
if (-not $via) {
    Bad "via 25s 内未见 onActivityResumed"
} else {
    Ok "via 到 onActivityResumed"
    Info ("  {0}" -f $via)
}

Write-Host ''
Write-Host "  完成。产物在 $outDir" -ForegroundColor Cyan
