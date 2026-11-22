<#
.SYNOPSIS
  容器启动稳定性诊断：连续启动 N 次，逐轮记录 pidof / crash / Displayed 并保存日志。

.EXAMPLE
  .\tools\diag-boot-loop.ps1 -Rounds 5 -Tag baseline
#>
[CmdletBinding()]
param(
    [string]$Serial = "10AFA8169Z0039G",
    [int]$Rounds = 5,
    [string]$Tag = "boot",
    [int]$WaitSec = 9,
    [string]$Out = "probe-reports",
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'

# ==================== 复位守卫 ====================
# 本脚本每轮都会 force-stop + 清 logcat，会破坏正在进行的现场定位。
if (-not $Reset) {
    Write-Host "[复位守卫] 将执行：每轮 force-stop 容器 + 清 logcat。" -ForegroundColor Yellow
    Write-Host "[复位守卫] 已停止。确认要复位请加 -Reset 重新执行。" -ForegroundColor Yellow
    return
}
# ==================================================
$HostPkg = 'com.nextvm.app'
$script:adbArgs = @('-s', $Serial)
function A { $all = $script:adbArgs + $args; & adb @all }

New-Item -ItemType Directory -Force -Path $Out | Out-Null
$stamp = Get-Date -Format 'HHmmss'

Write-Host "== $Tag : 连续 $Rounds 轮 ==" -ForegroundColor Cyan
$rows = @()
for ($i = 1; $i -le $Rounds; $i++) {
    A shell am force-stop $HostPkg | Out-Null
    Start-Sleep -Milliseconds 1200
    A logcat -b all -c | Out-Null

    $t0 = Get-Date
    A shell monkey -p $HostPkg -c android.intent.category.LAUNCHER 1 | Out-Null
    Start-Sleep -Seconds $WaitSec

    $pidNow = ((A shell pidof $HostPkg) -join '').Trim()
    $logPath = Join-Path $Out "boot-$Tag-$stamp-r$i.txt"
    cmd /c "adb -s $Serial logcat -d -b all -v threadtime > `"$logPath`" 2>nul" | Out-Null

    $l = Get-Content $logPath -Encoding utf8
    $crash = ($l | Select-String "am_crash").Count
    $disp = ($l | Select-String "Displayed com.nextvm.app").Count
    $rt = ($l | Select-String "name: RenderThread").Count
    $act = ($l | Select-String "wm_on_resume_called|wm_on_create_called").Count
    $sec = ($l | Select-String "SecurityException|Permission Denial|permission denied").Count

    $state = if ($pidNow -and $disp -gt 0 -and $crash -eq 0) { 'OK' }
             elseif ($pidNow -and $crash -gt 0) { 'CRASH-BUT-ALIVE' }
             elseif ($pidNow) { 'ALIVE-NO-DISPLAY' }
             else { 'DEAD' }

    $rows += [pscustomobject]@{ Round = $i; State = $state; Pid = $pidNow; Crash = $crash; Displayed = $disp; RenderThread = $rt; PermErr = $sec }
    Write-Host ("  r{0}: {1,-15} pid={2,-7} crash={3} displayed={4} rt={5} permErr={6}" -f $i, $state, $pidNow, $crash, $disp, $rt, $sec)
    if ($i -lt $Rounds) { A shell am force-stop $HostPkg | Out-Null; Start-Sleep -Milliseconds 800 }
}

Write-Host "== 汇总 ==" -ForegroundColor Cyan
$rows | Group-Object State | ForEach-Object { Write-Host ("  {0}: {1}/{2}" -f $_.Name, $_.Count, $Rounds) }
$okRate = [math]::Round((($rows | Where-Object State -eq 'OK').Count) / $Rounds * 100)
Write-Host ("  成功率: {0}%" -f $okRate) -ForegroundColor $(if ($okRate -eq 100) { 'Green' } else { 'Yellow' })
Write-Host ("  日志: $Out/boot-$Tag-$stamp-r*.txt")
