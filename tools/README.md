# tools/ 脚本索引

> 每个脚本都标注了**状态**：`在用` / `备用` / `一次性`。
> 过时的（`_rep.py`、`live-logcat.mjs`）已删除——后者是"后台 logcat 进程"方案，在一次性 pwsh 里活不过一次调用，已被「设备端 nohup 采样」取代。

## 探针采集与对比

| 脚本 | 状态 | 用途 |
| --- | --- | --- |
| `extract-report.py` | **在用** | 从 logcat 还原 base64 分块成 JSON。`python extract-report.py <logcat.txt>` |
| `probe-matrix.py` | **在用** | **多环境对比**：输入多份报告，输出 markdown 矩阵表，`--only-diff` 只看差异 |
| `collect-probe.ps1` | **在用** | 触发 + 采集（带 `-Reset` 守卫） |
| `diff-report.py` | 备用 | 两份报告逐项 diff（命令行视图） |
| `make-record.py` | 在用 | 把报告转成 `docs/records/*.md` 归档 |

## 设备自动化

| 脚本 | 状态 | 用途 |
| --- | --- | --- |
| **`gaia-test.ps1`** | **在用（待验证）** | **Gaia 全流程自动化**：启动 Gaia → 找探针图标 → 点「授予」→ 等进程 → 抓报告。坐标全靠 `uiautomator dump` 动态查询 |
| `auto-test.ps1` | 备用 | 端到端自动测试（带守卫） |
| `sample-longpress.sh` | **在用** | **设备端**采样器：每 0.4s 记 pid/窗口，专治「adb 被 ANR 阻塞抓不到现场」 |
| `diag-boot-loop.ps1` | 备用 | 启动循环诊断 |

### `gaia-test.ps1` 的关键设计（值得保留的片段）

```powershell
# 1) 坐标动态查询，不硬编码
function FindNodeCenter([xml]$Ui, [string]$Needle) { ... }

# 2) 轮询等元素，不用固定 sleep
function WaitForAny([string[]]$Candidates, [int]$TimeoutSec, [string]$Tag) { ... }

# 3) 权限窗口文案多变，给候选列表
$grant = WaitForAny @('授予','允许','同意','确定','始终允许') 10 'grant'

# 4) 失败时打印当前 UI 可见文本，便于当场定位
ShowUiLabels $last
```

**为什么这几段要保留**：Gaia 的布局和文案会随版本变，硬编码坐标的脚本活不过一次更新；轮询 + 候选文案 + 失败打印 UI 的组合是最省心的写法。

## CI / GitHub

| 脚本 | 状态 | 用途 |
| --- | --- | --- |
| `gh-api.mjs` | 在用 | GitHub API GET（走 127.0.0.1:12450 代理，token 不打印） |
| `gh-clean-release.mjs` | 在用 | 清理 `apk-latest` Release 的旧 assets，`--apply` 才真删 |
| `push-https.ps1` | 备用 | SSH 不通时的 token+HTTPS 推送（详见 AGENTS.md 方法 2） |

## 文档 / 代码分析

| 脚本 | 状态 | 用途 |
| --- | --- | --- |
| `collect-graph-data.py` | 在用 | 统计核心类的行数/函数数/依赖强度，供 `docs/diagrams/*.dot` 使用 |
| `fetch-aosp.mjs` | 备用 | 抓 AOSP 文档页（走代理） |
| `html2txt.py` | 备用 | HTML 转纯文本 |
| `flatten_ui.py` | 一次性 | 批量把 `RoundedCornerShape` 换成 `RoundedCornerShape(0.dp)`（p9 用过；主题层改完后一般不再需要） |

## 已删除（记录原因，避免重复尝试）

| 脚本 | 删除原因 |
| --- | --- |
| `live-logcat.mjs` | "后台 logcat 进程 + fd 重定向"在一次性 pwsh 会话里**活不过一次调用**（宿主机杀进程树）。正确做法是 `adb shell "nohup logcat ... &"` 让进程跑在**设备端**，即 `sample-longpress.sh` 的思路 |
| `_rep.py` | 临时调试脚本（打印报告字段），已被 `probe-matrix.py` 覆盖 |
