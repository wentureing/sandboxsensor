<#
.SYNOPSIS
  探针全自动测试：T1 容器外 → T2a 容器内 → T2b 容器重启后。

.NOTES
  踩过的坑已固化：
  * 后台 logcat 活不过一次调用 → 全程"触发 → 等 → dump"
  * vivo logcat 必须 -b all
  * 容器自己把 /sdcard 重定向到沙箱，installApp 读不到 → 必须放 app 私有目录
  * 宿主机 stdin 重定向不可靠（只传 200 字节）→ 用设备端 cat 管道

.EXAMPLE
  .\tools\device\auto-test.ps1 -ProbeApk .\dist\probe-xxx.apk
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ProbeApk,
    [string]$ContainerApk,
    [string]$Serial = "10AFA8169Z0039G",
    [string]$Out = "probe-reports",
    # 2026-11-26 实测：探针从 LAUNCH_ACTIVITY 到 probe-report.json 更新只需 **2s**，
    # 原默认 30s 是为了覆盖 watchdog 的 stackdump（+0.3..+20s），但报告本身不等它。
    # D03 已改成三态（not-yet / missing），所以不必为了 stackdump 拖长等待。
    [int]$WaitSec = 12,
    [int]$HumanWaitSec = 0,
    [switch]$SkipT1,
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'

# ==================== 复位守卫 ====================
# 本脚本含清 logcat / force-stop / uninstall 等复位动作，会破坏正在进行的现场定位。
# 默认不执行；需要时显式加 -Reset。
if (-not $Reset) {
    Write-Host "[复位守卫] 将执行：清 logcat / force-stop / uninstall，可能破坏现场。" -ForegroundColor Yellow
    Write-Host "[复位守卫] 已停止。确认要复位请加 -Reset 重新执行。" -ForegroundColor Yellow
    Write-Host "[复位守卫] 只读采集请用：adb logcat -d -b all > file（不要加 -c）" -ForegroundColor Gray
    return
}
# ==================================================
$Pkg = 'com.sandbox.probe'; $Act = "$Pkg/.ProbeActivity"
$HostPkg = 'com.nextvm.app'; $Cmd = "$HostPkg/.CmdReceiver"
$script:adbArgs = @('-s', $Serial)

New-Item -ItemType Directory -Force -Path $Out | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

function A { $all = $script:adbArgs + $args; & adb @all }
function Ok($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function W($m) { Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Info($m) { Write-Host "  $m" -ForegroundColor Gray }

function Wait-Marker([string]$Pattern, [int]$Seconds) {
    # 轮询间隔从 5s 降到 1s：实测报告 2s 就绪，粗粒度轮询只会白等。
    for ($i = 0; $i -lt [Math]::Max(1, [int]($Seconds / 1)); $i++) {
        Start-Sleep -Seconds 1
        if ((A logcat -d -b all 2>$null) -match $Pattern) { return $true }
    }
    return $false
}

function Stage([string]$name, [int]$WaitSec, [int]$Expect) {
    $hit = Wait-Marker -Pattern "REPORT v1 end|EARLY v1 end" -Seconds $WaitSec
    Start-Sleep -Seconds 2
    $log = Join-Path $Out "$name-$stamp.txt"
    cmd /c "adb -s $Serial logcat -d -b all -v threadtime > `"$log`" 2>nul"
    $l = Get-Content $log -Encoding utf8
    $ph = ($l | Select-String "PROBE\s+:\s+PHASE").Count
    $rp = ($l | Select-String "REPORT v1 part").Count
    $cr = ($l | Select-String "am_crash").Count
    Info ("{0}: PHASE={1} REPORT块={2} crash={3} marker={4}" -f $name, $ph, $rp, $cr, $(if ($hit) { 'yes' } else { 'timeout' }))
    $null = & python tools\probe\extract-report.py $log 2>&1 | Where-Object { $_ -match 'report-|early-' } | ForEach-Object { Info ("  " + $_.Trim()) }
    return [pscustomobject]@{ Name = $name; Phases = $ph; Chunks = $rp; Crashes = $cr; Log = $log }
}

# ---------------------------------------------------------------- 0
Info ("device={0} sdk={1} page={2}" -f `
    ((A shell getprop ro.product.model) -join '').Trim(),
    ((A shell getprop ro.build.version.sdk) -join '').Trim(),
    ((A shell getconf PAGESIZE) -join '').Trim())

$results = @()

# ---------------------------------------------------------------- 1 T1
if (-not $SkipT1) {
    Write-Host "== T1 容器外 ==" -ForegroundColor Cyan
    A logcat -b all -c | Out-Null
    A uninstall $Pkg | Out-Null
    $r = (A install -r -t $ProbeApk 2>&1) -join ' '
    if ($r -notmatch 'Success') { W "install failed: $r" } else {
        A shell am start -n $Act | Out-Null
        $results += Stage 't1' $WaitSec 1
    }
}

# ---------------------------------------------------------------- 2 T2a
Write-Host "== T2a 容器内 ==" -ForegroundColor Cyan
if ($ContainerApk -and (Test-Path $ContainerApk)) {
    A install -r -t $ContainerApk | Out-Null
    if ($HumanWaitSec -gt 0) {
        W "首次启动如需人工授权，请在 $HumanWaitSec s 内完成"
        A shell monkey -p $HostPkg -c android.intent.category.LAUNCHER 1 | Out-Null
        Start-Sleep -Seconds $HumanWaitSec
    }
}
if (((A shell pm list packages $HostPkg) -join '') -notmatch $HostPkg) { throw "容器未安装" }

A uninstall $Pkg | Out-Null                      # guest 必须未安装
A shell run-as $HostPkg mkdir -p files/inbox | Out-Null
A push $ProbeApk /data/local/tmp/probe.apk | Out-Null
A shell "cat /data/local/tmp/probe.apk | run-as $HostPkg sh -c 'cat > files/inbox/probe.apk'" | Out-Null
$sz = ((A shell "run-as $HostPkg ls -l files/inbox/probe.apk") -join '')
Info ("private apk: " + $sz.Trim())

A logcat -b all -c | Out-Null
$ir = (A shell am broadcast -n $Cmd -a com.nextvm.cmd.INSTALL --es path /data/user/0/$HostPkg/files/inbox/probe.apk 2>&1) -join ' '
Start-Sleep -Seconds 6
$inst = (A logcat -d -b all | Select-String "CmdReceiver: INSTALL" | Select-Object -Last 1)
if ($inst -and $inst.Line -match 'Success') { Ok "INSTALL 成功" } else { W ("INSTALL 结果: " + ($inst.Line ?? 'none')) }

$lr = (A shell am broadcast -n $Cmd -a com.nextvm.cmd.LAUNCH --es pkg $Pkg 2>&1) -join ' '
if ($lr -match 'Broadcast completed') { Ok "LAUNCH 已发出" }
$results += Stage 't2a' $WaitSec 1
A shell am broadcast -n $Cmd -a com.nextvm.cmd.UNINSTALL --es pkg $Pkg | Out-Null

# ---------------------------------------------------------------- 3 T2b
Write-Host "== T2b 容器重启后 ==" -ForegroundColor Cyan
A shell am force-stop $HostPkg | Out-Null
Start-Sleep -Seconds 3
A shell monkey -p $HostPkg -c android.intent.category.LAUNCHER 1 | Out-Null
Start-Sleep -Seconds 8
$alive = ((A shell pidof $HostPkg) -join '').Trim()
if (-not $alive) {
    W "容器主进程启动后已崩溃（已知 GPU 驱动问题），跳过 T2b"
} else {
    Ok "容器主进程 pid=$alive"
    A logcat -b all -c | Out-Null
    A shell am broadcast -n $Cmd -a com.nextvm.cmd.LAUNCH --es pkg $Pkg | Out-Null
    $results += Stage 't2b' $WaitSec 1
}

# ---------------------------------------------------------------- 汇总
Write-Host "== 汇总 ==" -ForegroundColor Cyan
$results | ForEach-Object { Info ("{0,-5} PHASE={1} chunks={2} crash={3}" -f $_.Name, $_.Phases, $_.Chunks, $_.Crashes) }
Info ("输出目录: $Out")
