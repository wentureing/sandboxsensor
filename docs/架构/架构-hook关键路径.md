# 架构：hook 关键路径矩阵

- 日期：2026-11-24（据当前代码核对；**替代** `架构-拦截能力层次.md` 中过时的行数与状态）
- 目的：每种 hook 的**注入点 → 生效范围 → 失效条件 → 观测点**，用于真机回归时判断"哪一环没生效"
- 依据：代码 grep + 各模块镜像文档

---

## 一、总表（12 种 hook）

| # | 种类 | 注入点（谁调） | 生效范围 | 失效条件 | 观测点 |
| --- | --- | --- | --- | --- | --- |
| **1** | **native GOT/PLT** | `VirtualEngine.initNativeHooks` → `NativeHookBridge.initNativeHooks` → `nativeInit()` | 启动时**已加载**的库（**排除** libart / libwebviewchromium） | 运行期新加载的库未 patch（L6 链断） | `initNativeHooks` 事件 |
| **2** | **Java RegisterNatives** | `NativeHookBridge.initNativeHooks` → `JavaIoHooks.install()` | `libcore.io.Linux.open/access` 等 | **默认关闭**（`debug.nextvm.javaIo=0`） | `javaIo installed=` |
| **3** | **系统服务代理** | `VirtualEngine` → `SystemServiceProxyManager.installAllProxies(context)` | guest 通过 `getSystemService` 拿到的实例 | 直接 `ServiceManager.getService` 绕过 | 无（Timber only） |
| **4** | **Binder 代理（缓存层）** | `VirtualEngine` → `ActivityBinderWrap.install(context)` | `ServiceManager.sCache` | 已 hold 的 binder 引用 | 无 |
| **5** | **ContentResolver 代理** | `VirtualEngine` → `ContentResolverProxy.install(context)` | `ContentResolver` 调用 | 直接用 provider client | 无 |
| **6** | **ActivityThread.mH** | `NextVmApplication` → `engine.installActivityThreadHookEarly` / `VirtualEngine` → `ActivityThreadHook.install()` | 主线程消息分发 | 子线程直接调 `ActivityThread` 方法 | `txn` / `launch` / `legacy` |
| **7** | **Instrumentation** | `ActivityThreadHook.hookInstrumentationIfNeeded()` | Activity 生命周期回调 | guest 自带 Instrumentation | `instr` |
| **8** | **ClassLoader 替换** | `ActivityThreadHook.handleLaunchActivityItem` / `LegacyMessageHandlers` → `replaceClassLoader()` | `LoadedApk.mClassLoader` | 已缓存的 Class | `cl replace enter` |
| **9** | **LoadedApk 路径注入** | `VirtualApplicationManager.bindViaFrameworkLoadedApk` → `injectGuestPathsIntoLoadedApk()` | `ApplicationInfo` 的 dataDir/sourceDir | 已缓存的 LoadedApk（v143 无效的原因） | `v143 datadir` |
| **10** | **系统属性覆盖** | `ProcSpoofing.spoofSystemProperty()` | `__system_property_get` 被 hook 的调用 | 直接读 `/dev/__properties__` | 无 |
| **11** | **`/proc` 伪造** | `ProcSpoofing.spoofProcSelf(pid, pkg)` | 被 hook 的 read 路径（`fakeFileContent`） | 直接 syscall 读 | `v142` 之外的 `/proc` 相关行 |
| **12** | **崩溃/自杀守卫** | `MainLooperGuard.install()` / `ProcessKillGuard.install()` | 主线程 uncaught / `System.exit` | `Process.killProcess`（拦不住） | `guard` |

---

## 二、关键路径详解（回归时按这个顺序查）

### 路径 A：native 层（1 + 2 + 10 + 11）

```
NextVmApplication.onCreate
  → VirtualEngine.initialize(context)
    → nativeHookBridge.initialize()            // 加载 libnextvm
    → nativeHookBridge.initNativeHooks(ctx)    // ← 关键分界
        ├─ nativeInit()                        // GOT patch: open/stat/access/__system_property_get
        ├─ JavaIoHooks.install()               // 可选：RegisterNatives（默认关）
        └─ 同步已有规则 nativeAddPathRedirection
```

**回归判据**：
- `debug-trace.log` 有 `initNativeHooks` 事件，且**不是** `skip: container main process`；
- `nativeGetRedirectCount()` > 0（规则已同步到 native）。

### 路径 B：规则注册（native 生效的前提）

```
guest 进程 bindApplication
  → initNativeHooks（路径 A）
  → GuestPathSetup.registerGuestRedirections(...)
      ├─ nativeHookBridge.setupExternalStorageRedirections()
      └─ nativeHookBridge.setupAppRedirections()   // 共 ~10 条
  → DebugTrace: v142 registered rules=N
```

**回归判据**：`v142 registered rules=N` 的 N 与 `getNativeRedirectCount()` **一致**。
（当前两者分两行打印，需人工对比 —— 见「待改进」）

### 路径 C：Activity 启动（6 + 7 + 8）

```
ActivityThread.handleMessage(EXECUTE_TRANSACTION)
  → ActivityThreadHook.handleMessage           // DebugTrace: txn msg.what=159
    → handleExecuteTransaction                  // DebugTrace: EXECUTE_TRANSACTION enter
      → handleLaunchActivityItem                // DebugTrace: LaunchActivityItem enter
          ├─ preRegisterActivityToken（主题/Token 预置）
          ├─ replaceClassLoader(guestPkg, ...)  // DebugTrace: cl replace enter
          └─ 注入 Instrumentation（instr）
```

**回归判据**：这三行**按顺序**出现；`cl replace enter` 的 pkg 是 guest 包名。

### 路径 D：legacy 消息（Android 9 以下 / Service / Receiver）

```
handleMessage
  ├─ LAUNCH_ACTIVITY(100) → handleLegacyLaunchActivity   // legacy 事件
  ├─ CREATE_SERVICE(114)  → handleCreateService          // legacy 事件
  └─ RECEIVER(113)        → handleReceiver               // legacy 事件
```

**回归判据**：跑一个带 Service/Receiver 的 guest，`legacy` 至少出现 1 次。

### 路径 E：路径解析（三层，按优先级）

| 层 | 手段 | 覆盖 | 观测 |
| --- | --- | --- | --- |
| **L-Java** | `VirtualContext` 重写（26 个方法） | 通过 Context 拿路径的代码（~90%） | `ctx` 事件 |
| **L-native GOT** | `redirect_path()` | 被 patch 的库（大多数 native 代码） | `v142 rules=N` |
| **L-syscall** | 仅日志（`armSeccompDiag`） | 直接 `svc` 的代码（**不拦截**） | 无 |

**回归判据**：Java 侧与 native 侧**必须一致**（同一路径两边算出同一结果）。

---

## 三、已知失效点（回归时最容易"看起来正常其实没生效"）

| # | 失效点 | 为什么 | 怎么确认 |
| --- | --- | --- | --- |
| 1 | **运行期新加载的库没被 patch** | `System.loadLibrary` 调用方是 `libnativeloader.so`（未 patch） | `debug-trace.log` 里没有新库的 patch 记录 |
| 2 | **直接 syscall 绕过所有 hook** | GOT patch 只影响库内调用 | 只能靠 `armSeccompDiag` 观察到 |
| 3 | **rules=0** | 主进程注册的规则传不到 guest（**进程级**状态） | `v142 registered rules=0` → 立刻定位 |
| 4 | **假 dataDir 对 MIME 无效** | `VirtualContext.super` 用的是容器自己的 `ContextImpl` 的 LoadedApk | W2 仍 `text/plain`（已实测） |
| 5 | **isolated 进程（WebView renderer）完全不受控** | 无手段 | `sandboxed_process0` 里任何 hook 都不生效 |

---

## 四、与旧文档的差异（`架构-拦截能力层次.md` 需更新的点）

| 旧文档说 | 现状 |
| --- | --- |
| `ActivityThreadHook` 2133 行 | **1755 行**（v151–v156 拆出 4 个模块到 `engine/activitythread/`） |
| L4 Java 层文件 API「Phase 1 是空的，`hookFileClass()` 只打日志」 | **已实现** `JavaIoHooks`（v148，RegisterNatives `libcore.io.Linux.open/access`），但**默认关闭**：`libjavacore.so` 无 `Linux_open` 导出 → 无法 dlsym 原实现 → 必然偏差 |
| 未提 `MainLooperGuard` / `ProcessKillGuard` / `ProcSpoofing` / `PathRedirectRegistry` | 均已拆出为独立模块（见各镜像文档） |
| L1「`VirtualContext`」 | 仍有效，但 `getDataDir()` 在 detached 模式下会返回合成路径（v147） |

---

## 五、待改进

| # | 事项 |
| --- | --- |
| 1 | 把「Kotlin 规则数」与「native 规则数」合并成一行打印（`rules kotlin=11 native=11`），一眼看出漂移 |
| 2 | 给 hook 3/4/5（系统服务/Binder/ContentResolver 代理）加最小观测点（当前只有 Timber） |
| 3 | 补 `initContentProviders` 与 `onActivityPreCreate` 的限流插桩 |
| 4 | 把本文的「失效点」表做成**探针**（现在只是文档） |
