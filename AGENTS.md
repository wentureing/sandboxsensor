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

## 排查问题的第一站：GuestBreadcrumb（不要跳过）

> **遇到「guest 行为异常但没崩溃 / 没栈 / 没日志」时，先看 breadcrumb，再决定往哪查。**

上游 NEXTVM 已在全链路埋了 **199 处** `GuestBreadcrumb`，它记录**有序、带时间戳、含被吞异常完整栈**的 guest 生命周期事件。专为"静默失败"设计。

| 取法 | 命令 / 路径 |
| --- | --- |
| logcat（最快） | `adb logcat -d -b all -v threadtime \| Select-String "BootCrumb"` |
| 文件（含完整栈） | `/data/user/0/com.nextvm.app/files/virtual/data/<instance>/files/breadcrumb-<pid>.log` |

**推荐排查顺序**：

```
1. BootCrumb 日志                              ← 先看这个
2. breadcrumb-<pid>.log 末尾                    ← 找"最后一步"+"被吞的异常"
3. 按症状分层：窗口/输入/路径/检测面（对应探针 P/N 系列）
4. 最后才看 native 栈（tombstone / bugreport）
```

**为什么会绕弯路**（本项目已发生的教训）：

| 症状 | 我们的错误路径 | breadcrumb 能省的 |
| --- | --- | --- |
| RenderThread SIGABRT | 一上来归因"Adreno 驱动缺陷"（查 Flutter/Chromium workaround），实际是容器 hook 污染 GPU 驱动 | 直接看到 guest 启动到哪一步 |
| 长按 ANR | 猜 `WindowManager`/`PopupWindow`/`Window.Callback` | 显示长按后走进哪个生命周期回调 |
| Via 主页源码 | 猜 MIME / 猜 WebView patch，实际是渲染在 isolated 进程 | 至少先排除 guest 启动链路问题 |

**一句话**：栈顶在驱动里，不代表病因在驱动里；breadcrumb 是正向记录过程，比从结果倒推可靠。

详细用法见 `docs/debugging-guide.md`。

## 大量精确修改前必须落盘修改计划

> **触发条件（满足任一即算"大量"）**：改动涉及 ≥3 个文件 / ≥50 行 /
> ≥2 个 Gradle 模块 / 删除或重命名文件 / 触碰 hook、路径重定向、权限判断。

**这类改动一律走两步，不许直接开改：**

### 第 1 步：落盘修改计划（改之前）

文件：`docs/changes/<YYYYMMDD>-<任务名>.md`

必须包含：

| 章节 | 内容 |
| --- | --- |
| **目标** | 一句话 |
| **影响范围** | 文件清单 + 每个文件的改动类型（新增/修改/删除） |
| **逐文件 diff 预览** | 改前/改后片段；**跨文件或有歧义时用 `diff -u` 格式** |
| **风险** | 回归点、可能破坏的既有行为 |
| **回退方案** | 具体到命令（`git revert <sha>` / 恢复某个文件） |
| **验证方法** | 可执行命令 + 判据 |

**生成方式（命令行，不手写猜测）**：

```powershell
# 方案 A：先改到工作区，用 git 生成真实 diff，再决定是否提交
git -C _ref/ShitJumpingVM diff > docs/changes/<date>-<task>.diff
git -C _ref/ShitJumpingVM diff --stat

# 方案 B：不确定时，先把"打算改什么"写成 patch 文本，用 --dry-run 校验
git -C _ref/ShitJumpingVM apply --check my.patch
```

**落盘后停下来等确认**：`docs/changes/<...>.md` 写完即报告"计划已就绪，请查看"，
**不要在同一条回复里继续改代码**（除非明确说"直接改"）。

### 第 2 步：改完后追加实际 diff（改之后）

在同一文件的 `## 实际变更` 章节追加：

```powershell
git -C _ref/ShitJumpingVM diff > docs/changes/<date>-<task>.actual.diff
git -C _ref/ShitJumpingVM diff --stat >> docs/changes/<date>-<task>.actual.diff
```

**`.actual.diff` 与计划中的"预期"不一致时，必须在文档里解释差异原因**。

### 为什么定这条约束

历史上已经发生过几次"边想边改、越改越偏"：
`DiagnosticsSink` 半成品、WebView hook 先加后回退、`CACHE_EPOCH` 基于错误诊断清理缓存。
**落盘 + 等确认** 是最便宜的纠错点。

## 其他约定

- **凭据**：`token.txt` 不入库、不读取内容到对话中；脚本内读取后立即用于请求
- **产物命名**：`<类型>-<7位sha>[-<abi>].apk`
- **容器 command 通道**：`adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.{INSTALL|LAUNCH|LAUNCH_ACTIVITY|UNINSTALL|LIST}`
  - `LAUNCH_ACTIVITY --es pkg <pkg> --es activity <完整类名>`：容器启动 guest 时**只解析 LAUNCHER**，要起非 LAUNCHER 页面必须用这条
  - 结果统一打到 `logcat -d -s CmdReceiver`
  - **导入豁免**（p18）：`/sdcard/Download/`、`/storage/emulated/0/Download/`、`/data/local/tmp/` 可直接 `INSTALL --es path`，不必再 push + run-as 复制
  - 其他路径仍需放到 app 私有目录（容器自身把 `/sdcard` 重定向到沙箱）
- **测试设备**：`10AFA8169Z0039G`（vivo V2463A / Android 15 / arm64-v8a / PAGESIZE=4096）
- **设备时钟**比电脑快约 2 分钟，日志按设备时间切片
