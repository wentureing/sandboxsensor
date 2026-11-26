<#
.SYNOPSIS
  采集 guest 启动链的 breadcrumb golden（一次启动 = 一个样本），用 STOP + LAUNCH。

.DESCRIPTION
  用途：B4-4（启动链拆分）前的**契约 golden**。只读 guest 目录、只发 STOP / LAUNCH / LIST；
  不发 force-stop / pm clear / uninstall（UNINSTALL 会删数据并换 instanceId，
  那样第二个样本就不是"同实例第二次启动"了）。

  为什么必须 STOP：`VirtualEngine.killProcess` 只把记录改成 KILLED，真实进程仍在
  （2026-11-26 修，`GuestProcessStopper` 负责 SIGKILL）。不停就 LAUNCH 会复用旧进程 ——
  而且一个 slot 进程会被多个 guest 复用（argv0 变成最后 bind 的那个），
  所以**不能靠 `ps` 里的进程名判断某 guest 是否在跑**，只能无条件 STOP。

  每次采样 = 新 breadcrumb 文件里**本 guest** 的 `breadcrumb begin pkg=<guest> pid=<pid>`
  到首个 `cb onActivityResumed`（无则到 `bind complete`）。
  不截到文件尾：v124/v131 每 5s 的周期性打点不属于启动链。

  输出两份（都在 -Out 下）：
    <tag>.log      截取段 —— 契约 golden 的正体
    <tag>.raw.log  本次新文件的全文 —— 供事后查证/补采事件

.NOTES
  重启容器后**等 ≥10s** 再采集：容器刚起时虚拟 PM 的包列表未就绪
  （见 docs/架构/架构-就绪依赖与早期窗口.md）。脚本会用 LIST 做一次引擎就绪探测，
  但它只证明引擎能应答，不证明"过了早期窗口"。

.EXAMPLE
  .\tools\device\capture-breadcrumb-golden.ps1 -Guest mark.via -Tag via-1
  .\tools\device\capture-breadcrumb-golden.ps1 -Guest com.sandbox.probe -Tag probe-1 `
      -Activity com.sandbox.probe.ProbeActivity
#>
[CmdletBinding()]
param(
    [string]$Serial = '10AFA8169Z0039G',
    [Parameter(Mandatory = $true)][string]$Guest,
    [Parameter(Mandatory = $true)][string]$Tag,
    # 空 = 用 LAUNCH（guest 的 LAUNCHER Activity）
    [string]$Activity = '',
    # LAUNCH 之后等 onActivityResumed 的上限
    [int]$WaitSec = 25,
    # STOP 之后等进程真正消失的上限
    [int]$StopWaitSec = 15,
    # 等引擎应答 LIST 的上限（容器刚重启时用）
    [int]$HostWaitSec = 15,
    [string]$Out = 'probe-reports/golden',
    # 采完不把 guest 停掉（默认停：保持设备干净，下次启动才有新 pid）
    [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'

$HostPkg = 'com.nextvm.app'
$DataRoot = 'files/virtual/data'   # 相对 app 私有目录（run-as 视角）

function Sh([string]$cmd) {
    $r = & adb -s $Serial shell $cmd 2>&1
    return (($r | Out-String) -replace "`r", '').Trim()
}
function Cmd([string]$action, [string]$extra = '') {
    Sh "am broadcast -n $HostPkg/.CmdReceiver -a com.nextvm.cmd.$action $extra" | Out-Null
}
function Info([string]$m) { Write-Host "  $m" -ForegroundColor Gray }
function Warn2([string]$m) { Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Die([string]$m) { throw "[capture-breadcrumb] $m" }

$esc = [regex]::Escape($Guest)

# ---------------------------------------------------------------- 0 容器就绪
# 判据：引擎能应答 LIST（CmdReceiver 起来 + 引擎已加载），而不是固定 sleep。
$hostPid = Sh "pidof $HostPkg"
if (-not $hostPid) { Die "容器未运行（$HostPkg 无进程），先启动容器并等 10s" }
$HostUid = Sh "stat -c %U /data/data/$HostPkg"      # 形如 u0_a380
if (-not $HostUid) { Die "拿不到容器 uid（$HostPkg）" }

$ready = $false
for ($i = 0; $i -lt $HostWaitSec; $i++) {
    Cmd 'LIST'
    Start-Sleep -Seconds 1
    if ((Sh "logcat -d -s CmdReceiver | tail -200") -match "APP pkg=$esc instance=") { $ready = $true; break }
}
if (-not $ready) { Warn2 "LIST 在 ${HostWaitSec}s 内没有列出 $Guest（引擎可能刚起，或实例确实没装）" }
else { Info "引擎就绪（LIST 已列出 $Guest）" }

New-Item -ItemType Directory -Force -Path $Out | Out-Null
$outDir = (Resolve-Path $Out).Path

# ---------------------------------------------------------------- 1 定位实例
$listing = Sh "run-as $HostPkg ls $DataRoot"
$cand = ($listing -split "`n") |
    ForEach-Object { $_.Trim() } |
    Where-Object { $_ -match "^$esc`_(\d+)$" } |
    Sort-Object { [long]($_ -replace '^.*_', '') }
$inst = $cand | Select-Object -Last 1
if (-not $inst) { Die "找不到 $Guest 的实例目录（$DataRoot 下），先 INSTALL" }
Info "instance=$inst"

$guestDir = "$DataRoot/$inst/files"

# ---------------------------------------------------------------- 2 STOP（无条件）
# STOP 只按 instance 杀"同 uid 且 cmdline == pkg"的进程。若该 slot 进程的 argv0
# 是别的 guest，stopApp 里的 GuestProcessStopper 找不到它 —— 这时新文件不会出现，
# 下面第 5 步会明确报"进程复用"，而不是静默采到旧 golden。
Cmd 'STOP' "--es instance $inst"
$alive = $true
for ($i = 0; $i -lt $StopWaitSec; $i++) {
    Start-Sleep -Seconds 1
    $others = @((Sh "ps -A -o PID,USER,NAME") -split "`n" |
        Where-Object { $_ -match "^\s*\d+\s+$([regex]::Escape($HostUid))\s+(?!$([regex]::Escape($HostPkg)))" })
    if ($others.Count -eq 0) { $alive = $false; break }
}
if ($alive) {
    Warn2 "STOP 后容器 uid 下仍有 guest 进程：$($others -join '; ')"
    Warn2 "若第 5 步报'进程复用'，先重启容器（am force-stop $HostPkg + am start）再采"
} else {
    Info "STOP 生效（容器 uid 下无 guest 进程）"
}

# ---------------------------------------------------------------- 3 记录启动前文件集合
function Get-CrumbFiles {
    return @((Sh "run-as $HostPkg ls $guestDir") -split "`n" |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match '^breadcrumb-\d+\.log$' })
}
$before = Get-CrumbFiles

# ---------------------------------------------------------------- 4 LAUNCH
$t0 = Get-Date
if ($Activity) {
    Info "LAUNCH_ACTIVITY $Guest/$Activity"
    Cmd 'LAUNCH_ACTIVITY' "--es pkg $Guest --es activity $Activity"
} else {
    Info "LAUNCH $Guest"
    Cmd 'LAUNCH' "--es pkg $Guest"
}

# ---------------------------------------------------------------- 5 等就绪（新文件 + 本 guest 的 begin 行）
$newFile = $null
$hitResume = $false
$reused = $false
for ($i = 0; $i -lt $WaitSec; $i++) {
    Start-Sleep -Seconds 1
    $fresh = @((Get-CrumbFiles) | Where-Object { $before -notcontains $_ })
    if ($fresh.Count -eq 0) {
        # 没新文件但已有 "(already active, kept)" → 进程复用，本次样本无效
        $old = @($before | Select-Object -Last 1)
        if ($old.Count -gt 0) {
            if ((Sh "run-as $HostPkg tail -5 $guestDir/$($old[0])") -match "already active, kept\) pkg=$esc") {
                $reused = $true
                break
            }
        }
        continue
    }
    $begin = Sh "run-as $HostPkg grep -l 'breadcrumb begin pkg=$Guest pid=' $guestDir/$($fresh -join ' ') 2>/dev/null"
    if (-not $begin) { continue }
    $newFile = ($begin -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ } | Select-Object -First 1)
    $tail = Sh "run-as $HostPkg tail -20 $newFile"
    if ($tail -match 'cb onActivityResumed') { $hitResume = $true; break }
}
$elapsed = [int]((Get-Date) - $t0).TotalSeconds
if ($reused) {
    Die ("进程复用：$Guest 的新 bind 落进了已有 breadcrumb 文件（STOP 没杀到承载进程）。" +
        "重启容器（am force-stop $HostPkg + am start）后等 10s 再采。")
}
if (-not $newFile) { Die "LAUNCH 后 ${WaitSec}s 内没有新的 breadcrumb 文件（guest 未真正启动？看 logcat -s CmdReceiver）" }

# 给 resume 之后再补一拍：resume 行与随后的 v129/v131 同毫秒级写入
Start-Sleep -Seconds 1

# ---------------------------------------------------------------- 6 取回全文
$raw = Join-Path $outDir "$Tag.raw.log"
# 取回路径：先由设备 shell 重定向到 /data/local/tmp（shell 身份可写），再 adb pull。
# 不用 `cmd /c adb exec-out ... > file`：宿主机路径含中文时 cmd 重定向会静默失败，
# 而 Set-Content 走文本解码，会改行尾/BOM（golden 要可逐字节 diff）。
$remoteTmp = "/data/local/tmp/crumb-$Tag.log"
Sh "run-as $HostPkg cat $newFile > $remoteTmp"
if (Test-Path $raw) { Remove-Item $raw -Force }
& adb -s $Serial pull $remoteTmp $raw 2>&1 | Out-Null
Sh "rm -f $remoteTmp"
if (-not (Test-Path $raw)) { Die "取回 $newFile 失败（$remoteTmp -> $raw）" }

# ---------------------------------------------------------------- 7 截取本样本段
$all = @(Get-Content $raw -Encoding utf8)
$startPat = "^\[\+\d+ms\] \[[^\]]+\] breadcrumb begin pkg=$esc pid=\d+"
$start = -1
for ($i = 0; $i -lt $all.Count; $i++) { if ($all[$i] -match $startPat) { $start = $i; break } }
if ($start -lt 0) { Die "文件里找不到本 guest 的 begin 行：$newFile" }

$end = -1
for ($i = $start; $i -lt $all.Count; $i++) {
    if ($all[$i] -match '\] cb onActivityResumed ') { $end = $i; break }
}
$endKind = 'resume'
if ($end -lt 0) {
    for ($i = $start; $i -lt $all.Count; $i++) {
        if ($all[$i] -match '\] bind complete ') { $end = $i; break }
    }
    $endKind = 'bind-complete'
    Warn2 '本段没有 onActivityResumed，退回到 bind complete'
}
if ($end -lt 0) { $end = $all.Count - 1; $endKind = 'eof'; Warn2 '既无 resume 也无 bind complete，保留到文件尾' }

$seg = $all[$start..$end]
$log = Join-Path $outDir "$Tag.log"
$seg | Set-Content $log -Encoding utf8NoBOM

# ---------------------------------------------------------------- 8 汇总（供人工挑契约）
$seq = $seg | ForEach-Object { ($_ -replace '^\[\+\d+ms\] \[[^\]]+\] ', '') }
Write-Host ''
Write-Host ("  {0}: instance={1} 文件={2}" -f $Tag, $inst, (Split-Path $newFile -Leaf)) -ForegroundColor Cyan
Write-Host ("  耗时={0}s resume={1} 截取到={2} 行数={3}" -f $elapsed, $hitResume, $endKind, $seg.Count)
Write-Host ("  -> {0}" -f $log) -ForegroundColor Green
Write-Host ("  -> {0}（全文）" -f $raw)
Write-Host ''
$seq | ForEach-Object { Write-Host "    $_" }

if ($seg.Count -lt 8) { Die "采集到的事件过少（$($seg.Count) 行），检查 guest 是否真的起来了" }

# ---------------------------------------------------------------- 9 收尾
if (-not $KeepRunning) {
    Cmd 'STOP' "--es instance $inst"
    Info "已 STOP $inst（-KeepRunning 可保留）"
}
