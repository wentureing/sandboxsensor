$ErrorActionPreference = 'Continue'
Set-Location "C:\Users\86177\Desktop\轻简虚拟环境开发"

$inst = (adb exec-out run-as com.nextvm.app ls files/virtual/data/ 2>&1) -replace "`r", ""
Write-Output "实例: $inst"

$offline = "files/virtual/data/$inst/sdcard/Android/data/mark.via/files/offline"
Write-Output ""
Write-Output "=== 放 3 个 .mht 到沙箱 offline 目录 ==="
adb shell "run-as com.nextvm.app mkdir -p $offline" 2>&1
adb shell "run-as com.nextvm.app sh -c 'echo aaa > $offline/probe1.mht'" 2>&1
adb shell "run-as com.nextvm.app sh -c 'echo bbb > $offline/probe2.mht'" 2>&1
adb shell "run-as com.nextvm.app sh -c 'echo ccc > $offline/probe3.mht'" 2>&1
adb shell "run-as com.nextvm.app ls -la $offline" 2>&1

Write-Output ""
Write-Output "=== 重启 via 触发重新列举 ==="
adb shell "am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg mark.via" 2>&1 | Select-Object -Last 1
Start-Sleep -Seconds 18

$vp = (adb shell "pidof mark.via" 2>&1).Trim()
Write-Output "  via pid: $vp"

Write-Output ""
Write-Output "=== 核心判据: list0 的 offline 结果（期望 3 entries）==="
adb logcat -d -b all --pid=$vp 2>&1 | Select-String "ufs list0.*offline" | Select-Object -Last 6 | ForEach-Object { "  $($_ -replace '^\S+\s+\S+\s+\d+\s+\d+\s+','')" }

Write-Output ""
Write-Output "=== readdir 是否报错（98be504 加的）==="
$rd = adb logcat -d -b all --pid=$vp 2>&1 | Select-String "readdir err="
if ($rd) { $rd | Select-Object -Last 5 | ForEach-Object { "  [有错] $($_ -replace '^\S+\s+\S+\s+\d+\s+\d+\s+','')" } } else { Write-Output "  [无 readdir 错误]" }

Write-Output ""
Write-Output "=== 所有 list0 调用的路径与结果（看全貌）==="
adb logcat -d -b all --pid=$vp 2>&1 | Select-String "ufs list0" | Select-Object -Last 12 | ForEach-Object { "  $($_ -replace '^\S+\s+\S+\s+\d+\s+\d+\s+','')" }
