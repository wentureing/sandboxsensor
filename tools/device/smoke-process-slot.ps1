# process-slot 冒烟自动化（PowerShell 版）
# 为什么不是 .mjs：本机受限沙箱下 Node 的 execFileSync 捕获 adb 输出会 EPERM（命名管道），
#   pwsh 直接调 adb 正常。规则 19 的例外（已有 .ps1 设备脚本保留、受限时可用 ps1）。
# 覆盖：容器就绪 / 首次 LAUNCH / 会话复用 / SELF_CHECK / 进程名作用域(T7) / legacy 回归 / 恢复 framework
# 用法：.\tools\device\smoke-process-slot.ps1 [-Serial 10AFA8169Z0039G]
# 退出码：0 = 全过；1 = 有 FAIL
param([string]$Serial = '10AFA8169Z0039G')

$ErrorActionPreference = 'Stop'
$Pkg = 'com.nextvm.app'
$Guest = 'mark.via'
$script:results = @()

function A {
    & adb -s $Serial @args 2>&1
}
function Sh([string]$cmd) { (A shell $cmd) -join "`n" }
function RunAs([string]$cmd) { (A shell "run-as $Pkg $cmd") -join "`n" }
function Bcast([string]$action, [string[]]$extra = @()) {
    (A shell 'am' 'broadcast' '-n' "$Pkg/.CmdReceiver" '-a' $action @extra) -join "`n"
}
function GuestPids {
    (Sh 'ps -A') -split "`n" |
        Where-Object { $_ -match [regex]::Escape($Guest) -and $_ -notmatch 'nextvm' } |
        ForEach-Object { ($_ -split '\s+')[1] } |
        Where-Object { $_ -match '^\d+$' }
}
function LastTrace([string]$pattern, [int]$n = 5) {
    $out = RunAs "grep -aE `"$pattern`" files/debug-trace.log"
    @(($out -split "`n") | Where-Object { $_.Trim() } | Select-Object -Last $n)
}
function BreadcrumbTail {
    $base = 'files/virtual/data'
    $dirs = @((RunAs "ls $base") -split "`n" | Where-Object { $_ -like "$Guest*" } | Sort-Object)
    if ($dirs.Count -eq 0) { return '' }
    $dir = "$base/$($dirs[-1])/files"
    $newest = (RunAs "ls -t $dir/breadcrumb-*.log | head -1").Trim()
    if (-not $newest) { return '' }
    ((RunAs "cat $newest") -split "`n" | Select-Object -Last 40) -join "`n"
}
function Check([string]$name, [bool]$ok, [string]$evidence = '') {
    $script:results += [pscustomobject]@{ name = $name; ok = $ok }
    $line = if ($ok) { 'PASS' } else { 'FAIL' }
    Write-Host ("{0}  {1}{2}" -f $line, $name, $(if ($evidence) { "  | $evidence" } else { '' }))
}

try {
    # 0) 容器就绪
    Sh "am start -n $Pkg/.MainActivity" | Out-Null
    Start-Sleep -Seconds 10
    $hostOk = (Sh 'ps -A') -match 'com\.nextvm\.app'
    Check 'T0 容器主进程在跑' $hostOk
    if (-not $hostOk) { throw 'container not running' }

    $apps = @((RunAs 'ls files/virtual/apps') -split "`n" | Where-Object { $_ -like "$Guest*" })
    $instanceId = if ($apps.Count -gt 0) { $apps[0] -replace '\.json$', '' } else { '' }
    Check 'T0.1 找到 guest 实例' ([bool]$instanceId) $instanceId
    if (-not $instanceId) { throw 'instance not found' }

    # 1) 干净起点：STOP 后首次 LAUNCH
    Bcast 'com.nextvm.cmd.STOP' @('--es', 'instance', $instanceId) | Out-Null
    Start-Sleep -Seconds 2
    Bcast 'com.nextvm.cmd.LAUNCH' @('--es', 'pkg', $Guest) | Out-Null
    Start-Sleep -Seconds 4
    $launch1 = LastTrace 'Launched:'
    $pid1 = @(GuestPids)
    Check 'T1 首次 LAUNCH 成功' (($launch1.Count -gt 0) -and ($pid1.Count -gt 0)) ($launch1 | Select-Object -Last 1)
    $stub1 = ''
    if ($launch1.Count -gt 0 -and $launch1[-1] -match 'stub (com\.nextvm\.app\.stub\.\S+)') { $stub1 = $Matches[1] }
    Check 'T1.1 首次分配 stub' ([bool]$stub1) $stub1

    # 2) 复用
    Bcast 'com.nextvm.cmd.LAUNCH' @('--es', 'pkg', $Guest) | Out-Null
    Start-Sleep -Seconds 3
    $reuse = LastTrace 'Reused live session'
    $pid2 = @(GuestPids)
    Check 'T2 再 LAUNCH 走复用' ($reuse.Count -gt 0) ($reuse | Select-Object -Last 1)
    Check 'T2.1 复用后 pid 不变' (($pid1.Count -gt 0) -and ($pid2.Count -gt 0) -and ($pid1[0] -eq $pid2[0])) "before=$($pid1 -join ',') after=$($pid2 -join ',')"

    # 3) SELF_CHECK
    Bcast 'com.nextvm.cmd.SELF_CHECK' | Out-Null
    Start-Sleep -Seconds 4
    $hook = @(($RunAsHook = RunAs 'grep -a hookcheck files/checks.log') -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 3)
    $okLine = if ($hook.Count -gt 0) { $hook[-1] } else { '' }
    Check 'T3 SELF_CHECK fail=0' ($okLine -match 'fail=0') $okLine

    # 4) T7 进程名作用域：主进程的 ProcTrace 不应出现 guest 包名
    $pt = @((RunAs 'grep -aE "\[com\.nextvm\.app\] \[ProcTrace\]" files/debug-trace.log') -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 5)
    $rewritten = @($pt | Where-Object { $_ -match ("name=" + [regex]::Escape($Guest)) })
    Check 'T4 主进程不再被改名（T7）' ($rewritten.Count -eq 0) $(if ($rewritten.Count -gt 0) { $rewritten[-1] } elseif ($pt.Count -gt 0) { $pt[-1] } else { 'no ProcTrace yet' })

    # 5) legacy 路径回归（B4-4f 搬的就是这段）
    Sh 'setprop debug.nextvm.frameworkBind 0' | Out-Null
    Bcast 'com.nextvm.cmd.STOP' @('--es', 'instance', $instanceId) | Out-Null
    Start-Sleep -Seconds 2
    Bcast 'com.nextvm.cmd.LAUNCH' @('--es', 'pkg', $Guest) | Out-Null
    Start-Sleep -Seconds 5
    $bc = BreadcrumbTail
    Check 'T5 legacy 路径生效' ($bc -match 'bind complete \(legacy path\)') $(if ($bc -match 'bind complete \(legacy path\)') { 'legacy bind complete' } else { 'no legacy marker' })
    Check 'T5.1 legacy attach enter/exit' (($bc -match 'legacy attachBaseContext enter') -and ($bc -match 'legacy attachBaseContext exit')) $(if ($bc -match 'legacy attachBaseContext enter') { 'enter/exit ok' } else { 'missing' })

    # 6) 恢复 framework
    Sh 'setprop debug.nextvm.frameworkBind 1' | Out-Null
    Bcast 'com.nextvm.cmd.STOP' @('--es', 'instance', $instanceId) | Out-Null
    Start-Sleep -Seconds 2
    Bcast 'com.nextvm.cmd.LAUNCH' @('--es', 'pkg', $Guest) | Out-Null
    Start-Sleep -Seconds 4
    $bc2 = BreadcrumbTail
    Check 'T6 恢复 framework 路径' ($bc2 -match 'bind complete \(framework path\)') $(if ($bc2 -match 'bind complete \(framework path\)') { 'framework ok' } else { 'no framework marker' })
}
catch {
    Write-Host "ERR $($_.Exception.Message)"
}

$failed = @($script:results | Where-Object { -not $_.ok })
Write-Host ""
Write-Host ("==== {0}/{1} PASS ====" -f ($script:results.Count - $failed.Count), $script:results.Count)
if ($failed.Count -gt 0) {
    Write-Host ("FAILED: " + (($failed | ForEach-Object { $_.name }) -join ' ; '))
    exit 1
}
exit 0
