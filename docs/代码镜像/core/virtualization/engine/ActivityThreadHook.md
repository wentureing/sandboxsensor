# 代码镜像：`core/virtualization/engine/ActivityThreadHook.kt`

> 对应代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/engine/ActivityThreadHook.kt`
> 拆分进度：v150 已拆出 `MainLooperGuard.kt`、`TransactionDiagnostics.kt`；
> 其余 10 个职责见文末「拆分计划」。

## 职责

**做什么**
- 在 guest 进程里把 `ActivityThread.mH`（主线程 Handler）的 `Callback` 换成自己的，
  拦截 `EXECUTE_TRANSACTION`(159) / `LAUNCH_ACTIVITY`(100) / `CREATE_SERVICE`(114) / `RECEIVER`(113)。
- 在 `LaunchActivityItem` 到达时，把 stub 组件**换成 guest 真实组件**：
  替换 classloader、注入 `LoadedApk`、预注册 activity token、解析 AppCompat 主题。
- 提供 `installInstrumentationHookEarly()`，让 `newActivity()` 从第一条事务起就被拦截。
- 维护 `uninstall()` 与进程级 `ProcessKillGuard`。

**不负责**
- ❌ 主 looper 的崩溃守卫 → `MainLooperGuard`
- ❌ 事务投递的观测/描述 → `TransactionDiagnostics`
- ❌ stub 组件的声明与分配（`StubRegistry`）、Binder 代理（`core:binder`）
- ❌ 资源实例本身（`VirtualResourceManager`）

## 函数

| 签名 | 作用 | 副作用 / 前提 |
| --- | --- | --- |
| `install(): Boolean` | 安装 `mH` hook + looper guard | 必须在 hidden API bypass 之后；幂等（`installed`） |
| `installInstrumentationHookEarly()` | 提前挂 `Instrumentation` | 必须在 `install()` 之后立刻调用 |
| `handleMessage(msg): Boolean` | 按 `msg.what` 分发 | **返回值语义见「偏差登记 #1」** |
| `handleExecuteTransaction(msg)` | 遍历 `ClientTransaction.getCallbacks()`，命中 `LaunchActivityItem` 即接管 | 反射字段名随 OS 版本变化 |
| `handleLaunchActivityItem(item, token)` | 替换 classloader / 注入 LoadedApk / 注册 token | 是 `ActivityThreadHook` 最重的路径 |
| `replaceClassLoader(pkg, instanceId, apkPath)` | 给 stub 记录换 guest classloader | — |
| `onActivityPreCreate(activity)` | Activity 创建前回调（context 包装等） | 由 Instrumentation 调用 |
| `uninstall()` | 还原 `mH` 的 Callback | — |
| `ProcessKillGuard.install()` | 防止 guest 进程被系统误杀 | 进程级单例 |

## 偏差登记

| # | 偏差 | 相对谁 | 类型 | 理由 / 待办 |
| --- | --- | --- | --- | --- |
| **1** | `Handler.Callback` 包装里**忽略 `handleMessage()` 的返回值**，总是再调 `originalCallback` | AOSP 的 `Callback` 语义（返回 `true` = 已消费） | ⚠️ **待修偏差** | `handleExecuteTransaction` 成功接管后仍会被 original 再处理一次，可能导致 Activity 创建两次或记录互相覆盖。修法：`if (handled) return true` |
| **2** | 主 looper 被**嵌套包装**（`Handler.post { while(true) Looper.loop() }`） | AOSP 的 `ActivityThread.main()` 只调一次 `Looper.loop()` | 🟡 **必要偏差（形式待优化）** | 需要捕获 GMS/Toast 的 UID mismatch，否则 guest 进程直接崩。但"嵌套 loop"让崩溃栈与真机不一致 → 治本方向是 `Thread.setDefaultUncaughtExceptionHandler`（见 `MainLooperGuard` 文档） |
| **3** | 吞掉 `SecurityException("... not in UID ...")` + Toast 窗口失败 | AOSP 会崩溃 | ✅ **必要偏差** | ToastPresenter 用宿主 UID 校验虚拟包名，必然 mismatch。已限定"两类信号 + 全 cause 链扫描"，其余照常崩溃 |
| **4** | `handleExecuteTransaction` 的异常兜底 `catch → return false` | AOSP 会让异常上抛 | ⚠️ **待修偏差** | 反射结构变化（OS 升级）时"接管静默失效"，表现为 stub 被启动/白屏。应区分「探测失败（容忍）」与「接管失败（记录/暴露）」 |
| **5** | 把 stub 组件的 class/classloader 换成 guest 的 | AOSP 直接加载清单声明的类 | ✅ **必要偏差** | 容器原理决定（guest 组件不在宿主清单里） |

## 约束与坑

1. **`mH` hook 必须在 hidden API bypass 之后**，否则 `findField(ActivityThread, "mH")` 拿不到。
2. **`installInstrumentationHookEarly()` 不能省**：晚于第一条 `EXECUTE_TRANSACTION` 会漏掉首个 Activity。
3. **所有反射字段名都要容错**：`mIntent`/`mIntents`/`mActivityToken`/`mLifecycleStateRequest`
   在 OS 升级时可能改名；取不到时应降级而不是抛异常（当前 `handleExecuteTransaction` 已是 catch-all，
   但会掩盖接管失败，见偏差 #4）。
4. **`ProcessKillGuard` 是进程级单例**，重复 `install()` 必须无副作用。
5. **调试**：所有关键分支都应写 `DebugTrace.event/fail`（v148 起 debug 包默认全量输出），
   便于在 `debug-trace.log` 里看"消息进来了没有 / 接管成功没有"。

## 拆分计划（后续批次）

| 目标文件 | 迁出的函数（当前行号区间） | 依赖 |
| --- | --- | --- |
| ✅ `MainLooperGuard.kt` | `installMainLooperCrashGuard` (147–221) | 无 |
| ✅ `TransactionDiagnostics.kt` | `traceDelivery` / `describeTransactionItem` (330–383) | `context` |
| `TransactionInterceptor.kt` | `handleExecuteTransaction` / `handleLaunchActivityItem` (255–328, 482–683) | context、stubRegistry、engine |
| `LaunchSupport.kt` | `findRunningInstanceId` / `registerPending*` / `probeLaunchResources` (462–481, 684–809) | engine |
| `ApkWatchdog.kt` | `startApkWatchdog` (810–966) | context、apkPath |
| `GuestClassLoaderSetup.kt` | `replaceClassLoader` / `restoreHostLoadedApk` / `getGuest*ForLaunch` (967–1147, 1303–1357) | engine |
| `ActivityTokenAndTheme.kt` | `preRegisterActivityToken` / `tryResolveAppCompatTheme` / `parseThemeFromApk` (1148–1284) | stubRegistry |
| `InstrumentationHook.kt` | `hookInstrumentationIfNeeded` / `onActivityPreCreate` / `NextVmInstrumentation` (1358–1783, 1820–2070) | engine |
| `LegacyMessageHandlers.kt` | `handleLegacyLaunchActivity` / `handleCreateService` / `handleReceiver` (1590–1783) | engine |
| `ProcessKillGuard.kt` | `ProcessKillGuard` (2071–2133) | 无 |
