# 项目约定（sandboxsensor）

> 本文件优先级高于全局 `~/.dsh/AGENTS.md`。冲突时以本文件为准。

## 仓库与 CI

| 仓库 | 角色 |
| --- | --- |
| `wentureing/sandboxsensor` | 主仓库：探针 + 文档 + **全部 CI** |
| `wentureing/ShitJumpingVM` | 容器骨架，只放代码，**无 CI** |

- 分支：`main`
- 所有 workflow 只在本仓库（`build-probe`、`build-container`）
- 产物必须是 `.apk`，走 **Release asset**（tag `apk-latest`），不用 artifact（会被强制 zip）

## 推送：两种方法

### 方法 1（常规，默认）：SSH

```powershell
# remote 必须是 SSH URL
git remote set-url origin git@github.com:wentureing/sandboxsensor.git

# 每次都必须在同一条命令里带上 GIT_SSH（每次调用都是新进程）
$env:GIT_SSH='C:\Windows\System32\OpenSSH\ssh.exe'; git push origin main
```

判断：

- `Hi <user>! You've successfully authenticated` → 通道正常（exit=1 是正常的，GitHub 不给 shell）
- `Connection timed out`（22 或 443）→ 网络问题，换方法 2
- `Permission denied (publickey)` → 密钥问题，报告用户

### 方法 2（备选）：token + HTTPS

仅在 SSH 不通时使用。脚本已就绪：`tools/push-https.ps1`

```powershell
git remote set-url origin https://github.com/wentureing/sandboxsensor.git
.\tools\push-https.ps1            # 默认推 main
```

约束：

- token 从 `token.txt` 读取（该文件在 `.gitignore` 里），**脚本不打印 token、不写进 URL、不做持久化配置**
- 用 `GIT_CONFIG_COUNT/GIT_CONFIG_KEY_0/GIT_CONFIG_VALUE_0` 传 `Authorization` header
- 必须加 `-c http.sslBackend=openssl`（受限环境 schannel 拿不到凭据）
- 若报 `Recv failure` / `Connection was reset` → 网络仍不通，需先修网络或代理

### 故障排查顺序

1. `ssh -T git@github.com`（8s 超时）→ 通就**用方法 1**
2. 不通 → `.\tools\push-https.ps1` → 通就**用方法 2**
3. 两者都不通 → 先修网络（本机代理 `127.0.0.1:12450`），再回到 1

## 接管控制时的硬约束

**不要破坏现场。** 任何会改变设备/应用状态的操作必须先获得许可：

| 动作 | 是否默认允许 |
| --- | --- |
| `adb logcat -d -b all`（只读 dump） | ✅ 允许 |
| `adb shell pidof` / `dumpsys` / `getprop` | ✅ 允许 |
| `adb shell input keyevent HOME` | ❌ 禁止（会打断用户定位） |
| `adb shell am force-stop` | ❌ 默认禁止 |
| `adb shell pm clear` / `adb uninstall` | ❌ 默认禁止 |
| `adb logcat -c`（清缓冲） | ❌ 默认禁止（丢失证据） |

脚本侧：`auto-test.ps1`、`collect-probe.ps1`、`diag-boot-loop.ps1` 均带**复位守卫**，不带 `-Reset` 会直接返回。
需要复位时显式传 `-Reset`。

采集一律用：`adb logcat -d -b all -v threadtime > file`（不带 `-c`）。

## 其他约定

- **凭据**：`token.txt` 不入库、不读取内容到对话中；脚本内读取后立即用于请求
- **产物命名**：`<类型>-<7位sha>[-<abi>].apk`
- **容器 command 通道**：`adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.{INSTALL|LAUNCH|UNINSTALL}`
  - 装 APK 必须放 app 私有目录：容器自身把 `/sdcard` 重定向到沙箱，读不到外部路径
- **测试设备**：`10AFA8169Z0039G`（vivo V2463A / Android 15 / arm64-v8a / PAGESIZE=4096）
- **设备时钟**比电脑快约 2 分钟，日志按设备时间切片
