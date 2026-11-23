<#
.SYNOPSIS
    安装容器 APK 并**等到真正生效**才算成功，避免"以为装上了"。

.DESCRIPTION
    背景：`adb install` 返回 Success 之后，包管理器仍可能处于 pending 状态；
    而容器/探针的实际可用性还要再等进程初始化。
    本脚本做四步验证：
      1. adb install（带超时，可能需要手机上点确认）
      2. 轮询 `dumpsys package <pkg> lastUpdateTime` 变化
      3. 轮询 `pm path <pkg>` 有值
      4. 打印最终 versionName / lastUpdateTime

.EXAMPLE
    .\tools\install-and-wait.ps1 -Apk dist\container-shitjumpingvm-74595e5.apk
#>
param(
    [Parameter(Mandatory = $true)][string]$Apk,
    [string]$Serial = '10AFA8169Z0039G',
    [string]$Package = 'com.nextvm.app',
    [int]$TimeoutSec = 300
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Apk)) { throw "APK not found: $Apk" }
$apkPath = (Resolve-Path $Apk).Path
$sizeMb = [math]::Round((Get-Item $apkPath).Length / 1MB, 1)

Write-Host "=== 安装 $([System.IO.Path]::GetFileName($apkPath)) ($sizeMb MB) ===" -ForegroundColor Cyan

$before = (& adb -s $Serial shell "dumpsys package $Package 2>/dev/null | grep lastUpdateTime") -join ''
$before = $before.Trim()
Write-Host "  安装前: $(if ($before) { $before } else { '<未安装>' })"

# 1) 先试 -r（固定 keystore 后应该能成功）
$t0 = Get-Date
$out = & adb -s $Serial install -r -t $apkPath 2>&1
$dt = [int]((Get-Date) - $t0).TotalSeconds
Write-Host "  install -r 耗时 ${dt}s -> $($out -join ' ')"

if ($out -join ' ' -match 'INSTALL_FAILED_UPDATE_INCOMPATIBLE') {
    Write-Host '  签名不匹配，卸载后重装（会丢沙箱数据）' -ForegroundColor Yellow
    & adb -s $Serial shell "am force-stop $Package" | Out-Null
    Start-Sleep -Seconds 2
    $u = & adb -s $Serial uninstall $Package 2>&1
    Write-Host "  uninstall -> $($u -join ' ')"
    Start-Sleep -Seconds 2
    $t0 = Get-Date
    $out = & adb -s $Serial install -t $apkPath 2>&1
    Write-Host "  install 耗时 $([int]((Get-Date) - $t0).TotalSeconds)s -> $($out -join ' ')"
}

if (-not ($out -join ' ' -match 'Success')) {
    throw "安装失败：$($out -join ' ')"
}

# 2) 轮询 lastUpdateTime 变化
Write-Host '  等待包管理器状态稳定...'
$deadline = (Get-Date).AddSeconds($TimeoutSec)
$after = ''
while ((Get-Date) -lt $deadline) {
    $after = ((& adb -s $Serial shell "dumpsys package $Package 2>/dev/null | grep lastUpdateTime") -join '').Trim()
    if ($after -and $after -ne $before) { break }
    Start-Sleep -Seconds 2
}
if (-not $after) { throw "超时：$Package 仍未出现在包管理器里" }

# 3) 轮询 pm path
$path = ''
while ((Get-Date) -lt $deadline) {
    $path = ((& adb -s $Serial shell "pm path $Package 2>/dev/null") -join '').Trim()
    if ($path -match 'package:') { break }
    Start-Sleep -Seconds 2
}
if (-not ($path -match 'package:')) { throw "超时：pm path $Package 无输出" }

# 4) 打印最终状态
$ver = ((& adb -s $Serial shell "dumpsys package $Package 2>/dev/null | grep -E 'versionName|versionCode'") -join ' | ').Trim()
Write-Host ''
Write-Host '=== 安装完成 ===' -ForegroundColor Green
Write-Host "  $after"
Write-Host "  $ver"
Write-Host "  $path"
