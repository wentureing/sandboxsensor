# tools/ 脚本索引

> 全部脚本按用途分到 5 个子目录。**路径变了**（`tools/xxx` → `tools/<子目录>/xxx`），
> 文档与 `AGENTS.md` 里的引用已批量更新。

```
tools/
├── device/     需要 adb、直接操作手机
├── probe/      探针报告的后处理（不碰设备）
├── release/    GitHub Release / Actions
├── git/        推送
└── misc/       一次性工具
```

---

## device/ —— 设备交互（最常用）

| 脚本 | 用途 | 典型调用 |
| --- | --- | --- |
| **`install-and-wait.ps1`** | **装 APK 并等到真生效**（四步验证） | `.\tools\device\install-and-wait.ps1 -Apk dist\container-xxx.apk` |
| **`gaia-test.ps1`** | **Gaia 全流程**：启动 Gaia → 找探针图标 → 点"授予" → 等进程 → 抓报告 | `.\tools\device\gaia-test.ps1` |
| `collect-probe.ps1` | 采集探针（带 `-Reset` 守卫） | `.\tools\device\collect-probe.ps1` |
| `auto-test.ps1` | 端到端自动测试（带守卫） | — |
| `diag-boot-loop.ps1` | 启动循环诊断 | — |
| `sample-longpress.sh` | **设备端**采样器（每 0.4s 记 pid/窗口），治「adb 被 ANR 阻塞」 | 设备上执行 |

### gaia-test.ps1 的关键设计（踩坑后的写法）

```powershell
# 1) 坐标动态查询，不硬编码（Gaia 布局会变）
function FindNodeCenter([xml]$Ui, [string]$Needle)

# 2) 轮询等元素，不用固定 sleep
function WaitForAny([string[]]$Candidates, [int]$TimeoutSec, [string]$Tag)

# 3) 权限窗口文案多变，给候选列表
$grant = WaitForAny @('授予','允许','同意','确定','始终允许') 10 'grant'
```

**⚠️ 实测坑（2026-11-23）**：Gaia 的权限页按钮是 **`确定` @ (540, 2005)**，
标题文本里也含"授予"字样 → **候选列表按文本匹配会先命中标题**，点到标题无效。
**正确做法**：匹配"确定"或按 bounds 取最下方的按钮。

---

## probe/ —— 探针数据后处理

| 脚本 | 用途 |
| --- | --- |
| **`extract-report.py`** | 从 logcat 还原 base64 分块成 JSON |
| **`probe-matrix.py`** | 多环境对比矩阵（`--only-diff` 只看差异） |
| `diff-report.py` | 两份报告逐项 diff |
| `make-record.py` | 报告转成 `docs/records/*.md` |

```powershell
python tools\probe\extract-report.py probe-reports\dump-xxxx.txt
python tools\probe\probe-matrix.py a.json b.json c.json --only-diff
```

---

## release/ —— GitHub Release / Actions

| 脚本 | 用途 |
| --- | --- |
| **`gh-api.mjs`** | GitHub API 客户端（GET / `--asset` 下载 / `--logs` 日志 / `--dispatch` 触发 workflow） |
| **`gh-clean-release.mjs`** | 清理 `apk-latest` 的旧 asset（`--apply` 才真删） |

```powershell
# 查运行 / Release
node tools\release\gh-api.mjs "/repos/wentureing/sandboxsensor/actions/runs?per_page=3"

# 下载 Release 里的某个 asset
node tools\release\gh-api.mjs --asset container-shitjumpingvm-<sha>.apk dist\xxx.apk

# 触发 workflow_dispatch（可指定容器 ref）
node tools\release\gh-api.mjs --dispatch container-build.yml main ref=ec12b31

# 只保留最新一组产物
node tools\release\gh-clean-release.mjs --keep container-xxx.apk --keep probe-yyy.apk --apply
```

**代理**：默认 `127.0.0.1:7897`（Clash），可用 `GH_PROXY_HOST` / `GH_PROXY_PORT` 覆盖。
**注意**：代理不稳时会返回 `HTTP 0`，重试 2–4 次即可。

---

## git/ —— 推送

| 脚本 | 用途 |
| --- | --- |
| `push-https.ps1` | SSH 不通时的 token+HTTPS 推送（详见 `AGENTS.md` 方法 2） |

---

## misc/ —— 一次性工具

| 脚本 | 用途 |
| --- | --- |
| `collect-graph-data.py` | 统计类行数/函数数/依赖强度，供 `docs/diagrams/*.dot` |
| `fetch-aosp.mjs` | 抓 AOSP/官方文档（走代理，支持 `format=TEXT` 的 base64） |
| `flatten_ui.py` | 批量把 `RoundedCornerShape` 换成 `0.dp`（p9 用过） |
| `html2txt.py` | HTML 转纯文本 |

---

## 常用组合（按场景）

### 场景 1：装容器 + 装 guest + 跑探针

```powershell
# 1) 装容器（需要手机上点确认，期间不操作）
.\tools\device\install-and-wait.ps1 -Apk dist\container-shitjumpingvm-<sha>.apk

# 2) 前置检查
adb shell pidof com.nextvm.app
adb shell run-as com.nextvm.app ls -1 files/virtual/data

# 3) 清空旧实例（每次删一个，N 个要 N 次）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.UNINSTALL --es pkg com.sandbox.probe
Start-Sleep 6

# 4) 装探针（/sdcard/Download 需 MANAGE_EXTERNAL_STORAGE=allow）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path /sdcard/Download/probe-v18.apk
Start-Sleep 12

# 5) 核对数量必须 = 1
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LIST

# 6) 启动（用 LAUNCH_ACTIVITY，不要用 LAUNCH）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH_ACTIVITY `
    --es pkg com.sandbox.probe --es activity com.sandbox.probe.UiTestActivity
Start-Sleep 15

# 7) 收集 + 提取
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.COLLECT_OUT
python tools\probe\extract-report.py probe-reports\dump-*.txt
```

### 场景 2：Gaia 测试

```powershell
# 前提：探针 APK 已在 /sdcard/Download/，用户在 Gaia 里导入
adb push dist\probe-xxx.apk /sdcard/Download/probe.apk

# 自动跑（含点"授予"）
.\tools\device\gaia-test.ps1
```

### 场景 3：只看 Release 状态

```powershell
node tools\release\gh-api.mjs "/repos/wentureing/sandboxsensor/releases/tags/apk-latest"
```

---

## 已删除（记录原因，避免重复尝试）

| 脚本 | 原因 |
| --- | --- |
| ~~`live-logcat.mjs`~~ | 后台 logcat 进程在一次性 pwsh 里活不过一次调用；正确做法是设备端 `nohup`（`sample-longpress.sh`） |
| ~~`_rep.py`~~ | 临时调试脚本，已被 `probe-matrix.py` 覆盖 |
