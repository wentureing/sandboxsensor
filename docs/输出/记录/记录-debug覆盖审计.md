# 审计：debug 观测是否覆盖关键路径与抽象层分界点

- 日期：2026-11-24
- 目的：真机回归前确认「行为变了能定位到哪一步」
- 结论：**核心链路原先几乎空白，已补 5 处插桩**

---

## 一、关键路径 × 观测点矩阵

| # | 关键路径 | 观测点（DebugTrace tag） | 补前 | 补后 |
| --- | --- | --- | --- | --- |
| 1 | 进程启动 → `bindApplication` 入口 | `bindApplication` | ✅ | ✅ |
| 2 | bind 完成 / 失败 | `bindApplication` | ✅ | ✅ |
| 3 | native hook 安装 `initNativeHooks` | `native` | ✅ | ✅ |
| 4 | 路径规则注册（v142） | `v142` | ✅ | ✅ |
| 5 | 假 dataDir（v143） | `v143` | ✅ | ✅ |
| 6 | `VirtualContext` 路径查询 | `ctx` / `appInfo` | ✅ | ✅ |
| 7 | **`ActivityThread.mH` 消息分发** | `txn` | ❌ | ✅ **新增** |
| 8 | **`EXECUTE_TRANSACTION` 拦截** | `txn` | ❌ | ✅ **新增** |
| 9 | **`LaunchActivityItem` 处理** | `launch` | ❌ | ✅ **新增** |
| 10 | **`replaceClassLoader`** | `cl` | ❌ | ✅ **新增** |
| 11 | **`hookInstrumentationIfNeeded`** | `instr` | ❌ | ✅ **新增** |
| 12 | legacy 三种消息（LAUNCH/CREATE_SERVICE/RECEIVER） | `legacy` | ✅ | ✅ |
| 13 | MainLooper 守卫安装 | `guard` | ✅ | ✅ |
| 14 | Java IO hook 安装 | `javaIo` | ✅ | ✅ |
| 15 | 客户探测（CMCC） | 仅 Timber | ⚠️ | ⚠️ 未改 |
| 16 | **`onActivityPreCreate`（Instrumentation 回调）** | — | ❌ | ❌ **仍缺** |
| 17 | **`initContentProviders`** | — | ❌ | ❌ **仍缺** |
| 18 | **WebView 初始化 / MIME 判定** | — | ❌ | ❌ **仍缺（故障点）** |

**仍缺的 3 项说明**：

| 缺项 | 为什么这次没补 |
| --- | --- |
| `onActivityPreCreate` | 在每帧/每 Activity 回调上，加日志会刷屏；应改成「每进程前 N 次」限流后再加 |
| `initContentProviders` | 属于 VAM 核心块（P1 拆分目标），拆分时一起加更合适 |
| WebView / MIME | **未知点** —— 目前还不知道该在哪加（W2 仍 `text/plain`）。这属于待排查范围，不是"漏了插桩" |

## 二、抽象层分界点检查

| 分界 | 观测手段 | 状态 |
| --- | --- | --- |
| **Java → native** | `initNativeHooks` 的成功/失败 + `getNativeRedirectCount()` | ✅ |
| **主进程 → guest 进程** | `DebugTrace` 每行带进程标记（`[p0]` / `[com.sandbox.probe]` / `[mark.via]`） | ✅ |
| **hook 前 → hook 后** | `v142 registered rules=N`（rules=0 即 hook 未生效） | ✅ |
| **Kotlin 侧规则表 ↔ native 侧 `redirect_path`** | `getPathRedirectSnapshot().size` vs `getNativeRedirectCount()` | ✅（但两者**不在同一行**打印，需人工对） |
| **LoadedApk 真身 ↔ guest 视图** | `v143 datadir pkg=… appInfo … -> mDataDir ->` | ✅ |
| **宿主 ActivityThread ↔ guest ActivityThread** | `cl replace enter` + `launch` 事件 | ✅ 本轮补 |
| **普通进程 ↔ :p0 进程** | 进程名在 trace 行首 | ✅ |

**发现的一个弱点**：Kotlin 规则数与 native 规则数**分开打印**，回归时无法一眼确认"两边一致"。
已在下方记为待改进项。

## 三、本轮补充（v159，行为提交）

| 文件 | 位置 | 内容 |
| --- | --- | --- |
| `ActivityThreadHook.kt:175` | `handleMessage` | `txn` / `mH msg.what=N`（detail 级） |
| `ActivityThreadHook.kt:196` | `handleExecuteTransaction` | `txn` / `EXECUTE_TRANSACTION enter` |
| `ActivityThreadHook.kt:378` | `handleLaunchActivityItem` | `launch` / `LaunchActivityItem enter` |
| `ActivityThreadHook.kt:864` | `replaceClassLoader` | `cl` / `replace enter pkg=…` |
| `ActivityThreadHook.kt:1256` | `hookInstrumentationIfNeeded` | `instr` / enter |

**纪律**：这些是**行为改动**（新增日志），单独提交，**不与任何重构混在一起**（两顶帽子）。

## 四、待改进（真机回归后再做）

| # | 事项 | 价值 |
| --- | --- | --- |
| 1 | 把「Kotlin 规则数」与「native 规则数」合并到**同一行**打印（如 `rules kotlin=11 native=11`） | 一眼看出两侧是否漂移，是路径类故障的第一判据 |
| 2 | `onActivityPreCreate` 加**限流**插桩（每进程前 20 次 + 之后每 100 次） | 覆盖 Instrumentation 回调而又不刷屏 |
| 3 | WebView 相关：先在 `shouldInterceptRequest` / `WebResourceResponse` 判定处加**探针式**插桩（不改行为） | 这是 MIME 故障的观测入口 |
| 4 | `debug-trace.log` 加一条**启动摘要**（进程、pkg、flags 快照、native 可用性） | 回归对比时先看这一行 |

## 五、回归时怎么用

```powershell
# 1) 拉 trace
adb shell "run-as com.nextvm.app cat files/debug-trace.log" > trace.txt   # 或 CO LLECT_OUT

# 2) 按 tag 看关键路径是否都走通（顺序即执行顺序）
Select-String trace.txt -Pattern '\[(bindApplication|v142|v143|txn|launch|cl|instr|legacy|guard)\]'
```

**判据**：
- 每个 tag 至少出现一次；
- `v142 registered rules=` 的 N 与 `nativeGetRedirectCount()` 一致；
- `txn` 出现次数 ≈ guest 启动的 Activity 数；
- 出现 `fail` 的行要逐条看原因。
