# 通过 HTTPS + token 推送，token 不打印、不出现在 URL、不做持久化配置。
# 仅从 token.txt 读取（该文件已被 .gitignore 排除）。
#
# 用法:
#   .\tools\git\push-https.ps1 [-Branch main] [-TokenIndex 1]
#
# 2026-11-27 修：token.txt 在 2026-11-24 起支持**多 PAT 按行存放**（gh-api.mjs 的 default=第1行、
#   content=第2行）。本脚本原先用 `Get-Content -Raw`，会把多行拼成一个含换行的 token ⇒ 必然 401。
#   现改为"取第 1 个非空、非注释行"，与 gh-api.mjs 的 default 行保持一致。
[CmdletBinding()]
param(
    [string]$RemoteUrl = "https://github.com/wentureing/sandboxsensor.git",
    [string]$Branch = "main",
    [int]$TokenIndex = 1,
    [string]$RepoRoot = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
)

$ErrorActionPreference = 'Stop'
$tokenFile = Join-Path $RepoRoot 'token.txt'
if (-not (Test-Path $tokenFile)) { throw "找不到 token.txt" }

# 与 gh-api.mjs 的 tokenLines() 同语义：非空、非 '#' 注释
$lines = @(Get-Content $tokenFile | ForEach-Object { $_.Trim() } |
    Where-Object { $_ -ne '' -and -not $_.StartsWith('#') })
if ($lines.Count -eq 0) { throw "token.txt 为空（或全是注释）" }
if ($TokenIndex -lt 1 -or $TokenIndex -gt $lines.Count) {
    throw "TokenIndex=$TokenIndex 越界（token.txt 有 $($lines.Count) 个 token）"
}
$token = $lines[$TokenIndex - 1]

$b64 = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("x-access-token:$token"))
$env:GIT_CONFIG_COUNT = '1'
$env:GIT_CONFIG_KEY_0 = "http.$RemoteUrl.extraheader"
$env:GIT_CONFIG_VALUE_0 = "AUTHORIZATION: basic $b64"

# schannel 在受限环境拿不到凭据，强制 openssl
try {
    $out = & git -C $RepoRoot -c http.sslBackend=openssl push $RemoteUrl $Branch 2>&1
    $code = $LASTEXITCODE
} finally {
    Remove-Item Env:GIT_CONFIG_COUNT, Env:GIT_CONFIG_KEY_0, Env:GIT_CONFIG_VALUE_0 -ErrorAction SilentlyContinue
}

# 无论成败，输出前都抹掉 token（防御性）
$clean = $out | ForEach-Object { $_ -replace [regex]::Escape($token), '***TOKEN***' }
$clean | Select-Object -Last 8

if ($code -ne 0) {
    $joined = ($clean | Out-String)
    if ($joined -match '401|Authentication failed|Invalid username') {
        Write-Host "[push-https] token 被拒（401）—— 检查 token.txt 第 $TokenIndex 行的 PAT 是否过期/无 repo 权限" -ForegroundColor Yellow
    } elseif ($joined -match 'Recv failure|Connection was reset|timed out|Could not resolve') {
        Write-Host "[push-https] 网络不通 —— 先试 SSH 通道，再回来；本机代理 127.0.0.1:12450" -ForegroundColor Yellow
    }
}
exit $code
