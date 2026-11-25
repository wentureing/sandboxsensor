# 现状：各级 hook 的验证手段矩阵 + 原项目测试完善程度

- 日期：2026-11-24
- 触发：用户问「目前测试只覆盖路径，各级 hook 是否成功怎么检查」+「原项目测试完善程度」
- 结论：**原项目几乎无测试**（0.54%），8 个大文件全部裸奔；hook 验证只能靠真机探针，
  且需要先建立"每级 hook 的最小判据"

---

## 一、原项目测试完善程度（实测）

| 项 | 数据 |
| --- | --- |
| `src/main` | **110 个 .kt / 45642 行** |
| `src/test` | **13 个 .kt / 983 行** |
| `androidTest` | **0 个** |
| 测试/主代码比 | **≈ 2.2%** |

**但这 983 行里大部分是我们 v157–v159 加的**：

| 来源 | 行数 |
| --- | ---: |
| 我们新增（PathRedirectRegistry / ProcSpoofing / MainLooperGuard / ProcessKillGuard / TransactionItemDescriber / CustomerProbes / GuestPathSetup / VirtualContext / Golden ×2） | **≈ 735** |
| **原项目自带**（`HostReadPolicyTest` 195 + `RobolectricSmokeTest` 53） | **≈ 248** |

→ **原项目自带的测试占比 = 248 / 45642 ≈ 0.54%**

### 8 个 >900 行的文件，**全部无测试**

| 文件 | 行数 | 测试 |
| --- | ---: | --- |
| `VirtualApplicationManager` | 3976 | ❌ |
| `SystemServiceProxyManager` | 3273 | ❌ |
| `VirtualEngine` | 2050 | ❌ |
| `ActivityThreadHook` | 1768 | ❌ |
| `ActivityManagerProxy` | 1295 | ❌ |
| `VirtualResourceManager` | 1127 | ❌ |
| `NativeHookBridge` | 995 | ❌ |
| `VirtualMultiProcessManager` | 947 | ❌ |

**这解释了为什么本项目只能靠真机探针** —— 原项目从设计上就没有测试网，
所有验证都是"装机 + 看日志"。

---

## 二、各级 hook 的验证手段矩阵

按 [`架构-hook关键路径.md`](../架构/架构-hook关键路径.md) 的 12 种 hook：

| # | hook | 纯 JVM | Robolectric | 真机探针 | 当前手段 | 缺口 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **native GOT/PLT** | ❌ | ❌ | ✅ | `initNativeHooks` 事件 + `nativeGetRedirectCount()` | 无自动化 |
| 2 | **Java RegisterNatives**（JavaIoHooks） | ❌ | ❌ | ✅ | `javaIo installed=` | 无自动化 |
| 3 | **系统服务代理** | ❌ | ⚠️ 可测（`ShadowContextImpl`） | ✅ | 仅 Timber | **无判据** |
| 4 | **Binder 代理（sCache）** | ❌ | ❌ | ✅ | 无 | **无判据** |
| 5 | **ContentResolver 代理** | ❌ | ⚠️ 可测 | ✅ | 无 | **无判据** |
| 6 | **ActivityThread.mH** | ❌ | ❌（无 mH 队列） | ✅ | `txn` / `launch` / `legacy` | 有日志无断言 |
| 7 | **Instrumentation** | ❌ | ⚠️ 有风险（见 androidx 评估） | ✅ | `instr`（限流） | 有日志无断言 |
| 8 | **ClassLoader 替换** | ❌ | ❌ | ✅ | `cl replace enter` | 有日志无断言 |
| 9 | **LoadedApk 路径注入** | ❌ | ❌ | ✅ | `v143 datadir` | 有日志无断言 |
| 10 | **系统属性覆盖** | ❌ | ❌ | ✅ | 无 | **无判据** |
| 11 | **/proc 伪造** | ✅ 可测（纯逻辑） | — | ✅ | `ProcSpoofingTest` 8 例 | 已覆盖**生成**，未覆盖**生效** |
| 12 | **崩溃/自杀守卫** | ✅ 部分 | — | ✅ | `MainLooperGuardTest` 7 + `ProcessKillGuardTest` 3 | 已覆盖分类逻辑，未覆盖真实拦截 |

### 三类手段的边界

| 手段 | 能证明 | 不能证明 |
| --- | --- | --- |
| **纯 JVM**（含 golden） | 纯函数逻辑（规则表、伪造内容生成、异常分类） | 任何"是否真的挂上了" |
| **Robolectric** | Android API 交互的正确分支（Context 路径、Provider 顺序） | native / IPC / 真实 WebView / 进程级顺序 |
| **真机探针** | 端到端真实生效 | 难以定位到具体环节（除非有分步日志） |

---

## 三、hook"是否成功"的最小判据（建议补齐）

**问题本质**：现在多数 hook 只有"进入日志"，没有"生效断言"。
日志说 `initNativeHooks installing hooks`，但**没证明每个 hook 真的挂上了**。

### 建议的判据（按 hook 优先级）

| # | hook | 建议判据 | 实现成本 |
| --- | --- | --- | --- |
| **1** | native GOT | **主动探针**：对一个已知被 patch 的函数（如 `open`）做一次"沙箱外路径"调用，看是否被重定向；结果写 `debug-trace` | 中（需 native 侧计数） |
| **2** | JavaIoHooks | 已有 `stats()`（total/fail/translated）+ `recent()` | ✅ 已有，只差**断言** |
| **3** | 系统服务代理 | 装完后取一次服务实例，比对 `instanceof 代理类` | 低 |
| **4** | Binder sCache | 取 `ServiceManager.sCache` 里某个 key，比对是否被替换 | 低 |
| **6** | ActivityThread.mH | 已有 `txn` 事件；补"hook 安装成功"日志（当前只有消息进来才打） | 低 |
| **8** | ClassLoader | `cl replace enter` 后再打一行 `cl now=<className>`，确认换成 guest 的 | 低 |
| **10** | 系统属性 | `spoofSystemProperty` 后立刻 `SystemProperties.get(key)` 读回比对 | 低 |
| **11** | /proc | 伪造后立刻读 `/proc/self/cmdline` 比对 | 低 |
| **12** | 守卫 | `install()` 后打一行 `guard active=true` | 低 |

**其中 5 项成本极低（各 1 行日志 + 1 次自检）**：3、4、8、10、11。

### 为什么"自检"比"进入日志"重要

| 日志 | 能证明 |
| --- | --- |
| `initNativeHooks installing hooks` | 代码执行到了这一行 |
| `javaIo installed=true` | `nativeInstall()` 返回了 true |
| **`selfcheck open(/sdcard/x) -> <sandbox>/x`** | **hook 真的拦截到了** |

前两者失败时你会看到"No such file"，后者失败时你看到的是"路径没变" —— **这才是可归因的**。

---

## 四、建议的下一步（按性价比）

| 顺 | 动作 | 成本 | 收益 |
| --- | --- | --- | --- |
| **1** | 给 5 个低成本 hook 加"自检"日志（3/4/8/10/11） | 低（各 1–3 行） | 真机测试时一眼看出哪个 hook 没挂上 |
| 2 | 把自检结果汇总成一行 `hookcheck ok=5 fail=0` | 低 | 回归时只看这一行 |
| 3 | 探针增加"H"系列（Hook 检查），与 W 系列并列 | 中 | 结构化对比 |
| 4 | `SystemServiceProxyManager` / `ContentResolverProxy` 补 Robolectric 测试 | 中 | 第 3、5 项 hook 有自动化 |

**第 1–2 项应在真机测试前做**，否则真机测试时仍然只能看"有没有进到某个函数"，
而不是"hook 有没有真的生效"。
