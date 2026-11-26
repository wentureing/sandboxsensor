# GuestDiagnosticsRegistration

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/startup/GuestDiagnosticsRegistration.kt`
> 来源：B4-4e 从 `VirtualApplicationManager.registerBreadcrumbCallbacks`（+ 顶层 `dumpV148ConnectProbe`）搬出（2026-11-26）

## 职责

**回答两个问题：guest 进程的诊断出口怎么装、Activity 生命周期回调怎么注册。**

- 负责（每进程一次）：
  1. 装诊断出口 —— `DiagnosticsHolder.install(DiagnosticsRouter(FileSink(checks.log)))`、`LifecycleRecorder.install`、
     `nativeHookBridge.dumpFsStats("bind:<pkg>")`；
  2. 启 watchdog（委托给 VAM 的 `startWatchdog`）；
  3. 注册 `ActivityLifecycleCallbacks`：7 个回调写 breadcrumb，并在 `onActivityCreated`/`onActivityResumed`
     上跑探针（`PluginProbes` / `LoginProbes` / `AutofillSuppressor` / `InitTrace.startLoad`）；
  4. 首次 resume 消费一次 guest 自检请求（debug 包）；
  5. 5s 一轮、最多 120 轮的登录按钮轮询（主线程 Handler）；
  6. `v148` native 连接探针的落盘（`dumpV148ConnectProbe`）。
- **不负责**：watchdog 本身（VAM）、`LoadedApk.mApplication` 补丁（`LoadedApkApplicationPatch`）、
  路径/权限 patch、`Application.onCreate` 触发。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `shouldConsumeSelfCheck(isDebugBuild: Boolean, alreadyDone: Boolean): Boolean` | 是否该消费自检请求 | **纯函数**（唯一的纯 JVM 可测点） |
| `install(app, nativeHookBridge, startWatchdog, existingRecord)` | 装诊断 + 注册回调 + 起轮询 | 每进程一次（`diagnosticsInstalled` / `loginProbeTimerStarted` 门闩）；任何异常都只记 breadcrumb，不影响 bind |
| `dumpV148ConnectProbe()`（private） | 抽空 native 连接探针环并追加到 `v148-connect-probe.log` | 无新事件时 no-op；文件目录必须已绑定 |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 4 个状态从 VAM 迁入 object | `guestDiagnosticsInstalled` / `guestSelfCheckDone` / `loginProbeLastActivity` / `loginProbeTimerStarted` | 中性偏差（VAM 是 `@Singleton`，object 亦每进程一份；四个字段原本就只在被搬函数内使用） | 每进程一次；`guest diagnostics installed` 只出现一次（golden C6 位置不变） |
| 2 个依赖改成回调 | `startWatchdog: () -> Unit`、`existingRecord: (Application) -> ApplicationRecord?`（原为直接调 VAM 私有方法 / 直接读 `applications` 表） | **必要偏差**（跨文件依赖，规则 10 允许的回调注入） | `existingRecord` 传入的表达式与原实现逐字相同（`values.firstOrNull { it.application === target }`） |
| `dumpV148ConnectProbe` 从顶层私有函数迁入 | 原为文件级 `private fun`，仅被本函数调用 | 中性偏差 | `v148-connect-probe.log` 内容不变 |
| 自检判据抽成 `shouldConsumeSelfCheck` | 原为 `if (!guestSelfCheckDone && DebugFlags.isDebugBuild())` | **必要偏差**（可测性），语义等价 | `GuestDiagnosticsRegistrationTest` S1–S3 |

**等价性说明（规则 10）**：本文件**无法用 `check-refactor-equivalence` 逐字对照**（同时做了状态迁移 + 两处回调注入 +
伴生函数迁入）。替代证据：
① 上面逐条偏差都有"与原实现的对应关系"；
② 行为由 golden **C6**（`Application.onCreate exit` → `v142-redirect` → `guest diagnostics installed` → `v130-watchdog started`）
与 **C8**（7 个 `cb onActivity*` 的顺序）逐条固定；
③ 探针 `D01–D05`（诊断通道五维）+ `SELF_CHECK` S5 覆盖诊断出口可用性。

## 约束与坑

- **顺序不能动**：`DiagnosticsHolder.install` 必须早于任何 `stage()` 调用（规则注册在 71ms、Holder 在 78ms →
  早于它的事件会被 NoOp 静默丢弃，这是历史上踩过的坑）；`dumpFsStats` 必须放在 `install` **之后**。
- **`startWatchdog` 用回调**：watchdog 的状态（`watchdogStarted` 等 5 个字段）仍在 VAM，别在本文件里另建一份。
- **`existingRecord` 只在 `onActivityCreated` 首次用**：用于 `PluginProbes.probePluginStatePhase(app, rec, "firstActivity")`，
  传 `null` 是合法的（探针内部处理）。
- **5s×120 轮轮询是既有行为**：v155 为了不漏"我的"tab 的按钮把 20s 调到 5s×10 分钟；
  它只在主线程跑只读层级遍历，且 breadcrumb 只在签名变化时记录 —— 不要为了"省电"擅自加长间隔（会漏检）。
- **自检只在 debug 包**：`shouldConsumeSelfCheck` 的 `isDebugBuild` 来自 `DebugFlags.ENABLED = BuildConfig.DEBUG`，
  release 里 R8 会把整段折叠掉（不要改成运行时判断）。
- `dumpV148ConnectProbe` 里 `GuestBreadcrumb.filesDir()` 为 null 时直接返回 —— 早期（attachFile 之前）调用是 no-op，属预期。
