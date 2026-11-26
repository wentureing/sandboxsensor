<#
.SYNOPSIS
  采集 guest 启动链的 breadcrumb golden（每次启动 = 一个独立样本）。

.DESCRIPTION
  用途：B4-4（启动链拆分）前的**契约 golden**。只读 guest 目录、只触发启动；
  不发 force-stop / pm clear。

  每次采样的内容 = 该 guest 最新 breadcrumb 文件里**最后一段**（最后一个
  `breadcrumb begin` 到文件尾），即最近一次 bind + 首个 Activity 的完整序列。

.EXAMPLE
  .\tools\device\capture-breadcrumb-golden.ps1 -Guest mark.via -Tag via-1
  .\tools\device\capture-breadcrumb-golden.ps1 -Guest com.sandbox.probe -Tag probe-1 `
      -Activity com.sandbox.probe.ProbeActivity -Restart
#>
param(
    [string]$Serial = '10AFA8169Z0039G',
    [Parameter(Mandatory = $true)][string]$Guest,
    [Parameter(Mandatory = $true)][string]$Tag,
    [string]$Activity = '',
    [switch]$Restart,
    [string]$Out = 'probe-reports/golden',
    [int]$WaitSec = 20
)

$ErrorActionPreference = 'Stop'
$HostPkg = 'com.nextvm.app'

function A { & adb -s $Serial @args }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

if ($Restart) {
    Write-Host "  重启实例（UNINSTALL + INSTALL $Guest）"
    A shell "am broadcast -n $HostPkg/.CmdReceiver -a com.nextvm.cmd.UNINSTALL --es pkg $Guest" | Out-Null
    Start-Sleep -Seconds 3
    $apk = if ($Guest -eq 'mark.via') { '/data/local/tmp/via.apk' } else { '/data/local/tmp/probe-new.apk' }
    A shell "am broadcast -n $HostPkg/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path $apk" | Out-Null
    # 等实例目录出现（最多 20s），别用固定 sleep
    $found = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        $d = A shell "run-as $HostPkg ls files/virtual/data/ 2>/dev/null" | Where-Object { <#
.SYNOPSIS
  采集 guest 启动链的 breadcrumb golden（每次启动 = 一个独立样本）。

.DESCRIPTION
  用途：B4-4（启动链拆分）前的**契约 golden**。只读 guest 目录、只触发启动；
  不发 force-stop / pm clear。

  每次采样的内容 = 该 guest 最新 breadcrumb 文件里**最后一段**（最后一个
  `breadcrumb begin` 到文件尾），即最近一次 bind + 首个 Activity 的完整序列。

.EXAMPLE
  .\tools\device\capture-breadcrumb-golden.ps1 -Guest mark.via -Tag via-1
  .\tools\device\capture-breadcrumb-golden.ps1 -Guest com.sandbox.probe -Tag probe-1 `
      -Activity com.sandbox.probe.ProbeActivity -Restart
#>
param(
    [string]$Serial = '10AFA8169Z0039G',
    [Parameter(Mandatory = $true)][string]$Guest,
    [Parameter(Mandatory = $true)][string]$Tag,
    [string]$Activity = '',
    [switch]$Restart,
    [string]$Out = 'probe-reports/golden',
    [int]$WaitSec = 20
)

$ErrorActionPreference = 'Stop'
$HostPkg = 'com.nextvm.app'

function A { & adb -s $Serial @args }
New-Item -ItemType Directory -Force -Path $Out | Out-Null

if ($Restart) {
    Write-Host "  重启实例（UNINSTALL + INSTALL $Guest）"
    A shell "am broadcast -n $HostPkg/.CmdReceiver -a com.nextvm.cmd.UNINSTALL --es pkg $Guest" | Out-Null
    Start-Sleep -Seconds 2
    $apk = if ($Guest -eq 'mark.via') { '/data/local/tmp/via.apk' } else { '/data/local/tmp/probe-new.apk' }
    A shell "am broadcast -n $HostPkg/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path $apk" | Out-Null
    Start-Sleep -Seconds 6
}

# 该 guest 的最新实例目录
$inst = (A shell "run-as $HostPkg ls files/virtual/data/" |
    Where-Object { $_ -like "$Guest*" } | Sort-Object | Select-Object -Last 1)
if (-not $inst) { throw "找不到 $Guest 的实例目录" }
$inst = $inst.Trim()
Write-Host "  instance=$inst"

$t0 = Get-Date
if ($Activity) {
    A shell "am broadcast -n $HostPkg/.CmdReceiver -a com.nextvm.cmd.LAUNCH_ACTIVITY --es pkg $Guest --es activity $Activity" | Out-Null
} else {
    A shell "am broadcast -n $HostPkg/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg $Guest" | Out-Null
}

# 轮询：等到 breadcrumb 里出现 onActivityResumed（首 Activity 已 resume）
$hit = $false
for ($i = 0; $i -lt $WaitSec; $i++) {
    Start-Sleep -Seconds 1
    $n = (A shell "run-as $HostPkg grep -c onActivityResumed files/virtual/data/$inst/files/breadcrumb-*.log 2>/dev/null" | Out-String)
    if ($n -match 'onActivityResumed' -or $n -match '\d') {
        $hit = $true
        break
    }
}
$elapsed = [int]((Get-Date) - $t0).TotalSeconds

# 取最后一段（最后一个 breadcrumb begin 到文件尾）：先定位最新文件，本地截取
$remote = "files/virtual/data/$inst/files"
$f = (A shell "run-as $HostPkg sh -c 'ls -t $remote/breadcrumb-*.log 2>/dev/null | head -1'").Trim()
if (-not $f) { throw "找不到 breadcrumb 文件（$remote）" }
Write-Host "  breadcrumb=$f"

$path = Join-Path (Resolve-Path $Out).Path "$Tag.log"
cmd /c "adb -s $Serial exec-out run-as $HostPkg cat $f > `"$path`" 2>nul"

# 本地截取：最后一个 "breadcrumb begin" -> 首个 "onActivityResumed"（无则到 bind complete）
# 不截到文件尾，否则会把每 5s 的 v131-autofill 周期性打点也算进 golden
$all = Get-Content $path
$idx = -1
for ($i = $all.Count - 1; $i -ge 0; $i--) { if ($all[$i] -match 'breadcrumb begin') { $idx = $i; break } }
$end = -1
if ($idx -ge 0) {
    for ($i = $idx; $i -lt $all.Count; $i++) {
        if ($all[$i] -match 'cb onActivityResumed') { $end = $i; break }
    }
    if ($end -lt 0) {
        for ($i = $idx; $i -lt $all.Count; $i++) {
            if ($all[$i] -match 'bind complete') { $end = $i; break }
        }
    }
}
if ($idx -ge 0 -and $end -ge $idx) {
    $all[$idx..$end] | Set-Content $path -Encoding utf8
} elseif ($idx -ge 0) {
    $all[$idx..($all.Count - 1)] | Set-Content $path -Encoding utf8
    Write-Warning "  $Tag 未找到 onActivityResumed/bind complete，保留到文件尾"
}
$lines = (Get-Content $path | Measure-Object -Line).Lines

Write-Host ("  {0}: resume={1} 耗时={2}s 行数={3} -> {4}" -f $Tag, $hit, $elapsed, $lines, $path)
if ($lines -lt 5) { throw "采集到的事件过少（$lines 行），检查 guest 是否真的启动了" }
 -like "$Guest*" }
        if ($d) { $found = $true; break }
    }
    if (-not $found) { throw "重启后仍无 $Guest 实例（INSTALL 可能失败）" }
}
# 该 guest 的最新实例目录
$inst = (A shell "run-as $HostPkg ls files/virtual/data/" |
    Where-Object { $_ -like "$Guest*" } | Sort-Object | Select-Object -Last 1)
if (-not $inst) { throw "找不到 $Guest 的实例目录" }
$inst = $inst.Trim()
Write-Host "  instance=$inst"

$t0 = Get-Date
if ($Activity) {
    A shell "am broadcast -n $HostPkg/.CmdReceiver -a com.nextvm.cmd.LAUNCH_ACTIVITY --es pkg $Guest --es activity $Activity" | Out-Null
} else {
    A shell "am broadcast -n $HostPkg/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg $Guest" | Out-Null
}

# 轮询：等到 breadcrumb 里出现 onActivityResumed（首 Activity 已 resume）
$hit = $false
for ($i = 0; $i -lt $WaitSec; $i++) {
    Start-Sleep -Seconds 1
    $n = (A shell "run-as $HostPkg grep -c onActivityResumed files/virtual/data/$inst/files/breadcrumb-*.log 2>/dev/null" | Out-String)
    if ($n -match 'onActivityResumed' -or $n -match '\d') {
        $hit = $true
        break
    }
}
$elapsed = [int]((Get-Date) - $t0).TotalSeconds

# 取最后一段（最后一个 breadcrumb begin 到文件尾）：先定位最新文件，本地截取
$remote = "files/virtual/data/$inst/files"
$f = (A shell "run-as $HostPkg sh -c 'ls -t $remote/breadcrumb-*.log 2>/dev/null | head -1'").Trim()
if (-not $f) { throw "找不到 breadcrumb 文件（$remote）" }
Write-Host "  breadcrumb=$f"

$path = Join-Path (Resolve-Path $Out).Path "$Tag.log"
cmd /c "adb -s $Serial exec-out run-as $HostPkg cat $f > `"$path`" 2>nul"

# 本地截取：最后一个 "breadcrumb begin" -> 首个 "onActivityResumed"（无则到 bind complete）
# 不截到文件尾，否则会把每 5s 的 v131-autofill 周期性打点也算进 golden
$all = Get-Content $path
$idx = -1
for ($i = $all.Count - 1; $i -ge 0; $i--) { if ($all[$i] -match 'breadcrumb begin') { $idx = $i; break } }
$end = -1
if ($idx -ge 0) {
    for ($i = $idx; $i -lt $all.Count; $i++) {
        if ($all[$i] -match 'cb onActivityResumed') { $end = $i; break }
    }
    if ($end -lt 0) {
        for ($i = $idx; $i -lt $all.Count; $i++) {
            if ($all[$i] -match 'bind complete') { $end = $i; break }
        }
    }
}
if ($idx -ge 0 -and $end -ge $idx) {
    $all[$idx..$end] | Set-Content $path -Encoding utf8
} elseif ($idx -ge 0) {
    $all[$idx..($all.Count - 1)] | Set-Content $path -Encoding utf8
    Write-Warning "  $Tag 未找到 onActivityResumed/bind complete，保留到文件尾"
}
$lines = (Get-Content $path | Measure-Object -Line).Lines

Write-Host ("  {0}: resume={1} 耗时={2}s 行数={3} -> {4}" -f $Tag, $hit, $elapsed, $lines, $path)
if ($lines -lt 5) { throw "采集到的事件过少（$lines 行），检查 guest 是否真的启动了" }

