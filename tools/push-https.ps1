# 通过 HTTPS + token 推送，token 不打印、不出现在 URL、不做持久化配置。
# 仅从 token.txt 读取（该文件已被 .gitignore 排除）。
#
# 用法: .\tools\push-https.ps1 [-RemoteUrl <url>] [-Branch main]
[CmdletBinding()]
param(
    [string]$RemoteUrl = "https://github.com/wentureing/sandboxsensor.git",
    [string]$Branch = "main",
    [string]$RepoRoot = (Split-Path $PSScriptRoot -Parent)
)

$ErrorActionPreference = 'Stop'
$tokenFile = Join-Path $RepoRoot 'token.txt'
if (-not (Test-Path $tokenFile)) { throw "找不到 token.txt" }

$token = (Get-Content $tokenFile -Raw).Trim()
if (-not $token) { throw "token.txt 为空" }

# 用 GIT_CONFIG_* 传 header，避免 token 出现在命令行 / URL
$b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("x-access-token:$token"))
$env:GIT_CONFIG_COUNT = '1'
$env:GIT_CONFIG_KEY_0 = "http.$RemoteUrl.extraheader"
$env:GIT_CONFIG_VALUE_0 = "AUTHORIZATION: basic $b64"

# schannel 在受限环境拿不到凭据，强制 openssl
$out = & git -C $RepoRoot -c http.sslBackend=openssl push $RemoteUrl $Branch 2>&1
$code = $LASTEXITCODE

# 无论成败，输出前都抹掉 token（防御性）
$out | ForEach-Object { $_ -replace [regex]::Escape($token), '***TOKEN***' } | Select-Object -Last 6

Remove-Item Env:GIT_CONFIG_COUNT, Env:GIT_CONFIG_KEY_0, Env:GIT_CONFIG_VALUE_0 -ErrorAction SilentlyContinue
exit $code
