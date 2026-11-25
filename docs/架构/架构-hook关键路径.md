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

### 总览：三条上游编号序列拼成完整的 guest 启动链

> **这三条不是我们归纳的，是上游代码里用 `// === STEP n ===` / `// Step n:` / `// Phase n:` 写出来的。**
> 历次拆分都完整保留了它们的注释与顺序（已核实：`ActivityThreadHook` L407–556、
> `VirtualApplicationManager` L566–631、`NativeHookBridge` L268/271）。

```
① native hook 安装（NativeHookBridge.initNativeHooks：Phase 1 → Phase 2）
   ↓  决定"路径重定向是否真的生效"
② guest 进程 bind → Application 启动（VirtualApplicationManager.createApplication：Step 1 → 6）
   ↓  决定"Application/Provider 是否按 AOSP 顺序起来"
③ Activity 启动（ActivityThreadHook.handleLaunchActivityItem：STEP 0 → 5）
   ↓  决定"Activity 能否用 guest 的类与资源"
④ 之后的运行时（mH 消息、Instrumentation 回调、路径解析三层）
```

**为什么按这个顺序查**：前一步失败会让后一步"看起来也失败"，但从后往前查会误判。
例如 Phase 2 失败时 Activity 仍能起来（只是文件读不到），容易被当成"路径规则没注册"。

### 路径 A：native 层（1 + 2 + 10 + 11）

**上游是怎么写的**（`NativeHookBridge.initNativeHooks`，旧代码 L889–905 的 `Phase 1/2` 注释）：

```kotlin
// Phase 1: Java-level IO hooks
val javaHooksInstalled = installJavaIoHooks()

// Phase 2: Try to load native library for PLT hooks (GOT patching)
nativeHooksAvailable = tryLoadNativeLibrary()
if (nativeHooksAvailable) {
    nativeInit()                                  // GOT patch
    nativeSetHostDataPrefix(ctx.dataDir)          // ← 必须紧接着设
    nativeSetVirtualDataRoot(ctx.filesDir/virtual) // ← 否则 native 不知道重定向到哪
}
```

| Phase | 内容 | 失败后果 |
| --- | --- | --- |
| **1** | Java 层 IO hook（`RegisterNatives`） | 只影响纯 Java IO（默认关闭） |
| **2** | native GOT patch（`nativeInit`） | **路径重定向整体失效** |
| 2 后续 | `nativeSetHostDataPrefix` + `nativeSetVirtualDataRoot` | 规则虽在，但**目标前缀为空** → 翻译成错误路径 |

**回归判据**：
- 有 `initNativeHooks ... installing hooks` 事件（**不是** `skip: container main process`）；
- `nativeGetRedirectCount()` > 0；
- 若 Phase 2 失败，`v142 registered rules` 仍会打印（Kotlin 侧成功），但**实际不生效** —— 这是最容易误判的点。

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

### 路径 C：Activity 启动 —— **STEP 0 → 5 注入序列**（本项目的核心关键路径）

> **这是上游代码里用 `// === STEP n ===` 编号显式写出的顺序**，不是推测。
> 代码位置：`ActivityThreadHook.handleLaunchActivityItem`（旧代码 L482–683，现 L407–556）。
> **顺序不能改**：后面每一步都依赖前一步的产物。

| STEP | 做什么 | 为什么必须在这个位置 | 观测点 |
| --- | --- | --- | --- |
| **前置** | 等 engine 到 `READY`（最多 10s） | 竞态：`ActivityThread` 可能比 engine 初始化更早 | — |
| **0** | 预注册 activity token 到 `mActivities` | 后面 `performLaunchActivity` 需要 `ActivityClientRecord` 已存在 | — |
| **1** | 交换 `ActivityInfo.name`（stub → guest 类名） | **必须先于 STEP 2**：否则 classloader 拿到的还是 stub 类名 | — |
| **1.5** | 开始 breadcrumb 见证 | 在 swap **完成前**就开始记录，才能捕获中途失败 | `BootCrumb` |
| **2** | 替换 `LoadedApk.mClassLoader` | 让 guest 的类能被加载（STEP 1 换了名字，这里换来源） | `cl replace enter` |
| **2.1** | 探测资源状态（v79） | 纯观测：确认资源是否真的从 guest APK 加载 | — |
| **2.2** | APK watchdog（v80/v81） | 纯观测：证实 APK 路径可用 | — |
| **2.5** | **启动 guest Application 生命周期（代码注释标 CRITICAL）** | **必须早于 Activity 创建**：`Application.onCreate` 先于 `Activity.onCreate` | `bindApplication` |
| **3** | 登记挂起的资源注入 | Activity `attach` 后才有 `Resources` 可换 | — |
| **4** | 登记挂起的 `VirtualContext` 包装 | `Activity.attach` 时替换 base context | — |
| **5** | 登记 Instrumentation hook（post-create 注入） | 之后的 `onActivityPreCreate` 才能注入 | `instr` |
| 收尾 | `return false` | 让**已修改**的事务继续走原生流程 | — |

**依赖链（改顺序会立刻坏）**：

```
STEP 1（换类名） ──必须先于──> STEP 2（换 classloader）
STEP 2（换 classloader） ──必须先于──> STEP 2.5（启动 Application）
STEP 2.5（Application.onCreate） ──必须先于──> Activity.onCreate（由 STEP 3/4/5 的挂起回调完成）
```

**回归判据**：
1. 按顺序出现 `cl replace enter` → `bindApplication` → `LegacyMessageHandlers`/`instr`；
2. STEP 2.5 若失败，现象是 **Activity 起来了但 Application 没起来**（很多应用会因此崩或白屏）；
3. STEP 0 若失败，现象是 **`ActivityClientRecord` 为 null**，后续 NPE。

### 路径 C2：Application 启动 —— **Step 1 → 6 序列**

> 同样来自上游代码的编号注释：`VirtualApplicationManager.createApplication`（旧代码 L566–631）。
> 对应 AOSP 的 `LoadedApk.makeApplication`，**Step 5 早于 Step 6 是 AOSP 语义**。

| Step | 做什么 | 关键调用 | 依赖 |
| --- | --- | --- | --- |
| **1** | 在 virtual context 上准备 `ApplicationInfo` | `buildApplicationInfo(...)` | — |
| **2** | 创建 guest 的 Application 实例 | `createApplication(...)` | Step 1 |
| **3** | 建 `ApplicationRecord` | `ApplicationRecord(...)` | Step 2 |
| **3.5** | 把 guest Application 设到 `VirtualContext` | `setGuestApplication(...)` | 让 `getApplicationContext()` 返回 guest 对象 |
| **4** | `attachBaseContext` | `swapInStockInstrumentation` + attach | Step 3.5 |
| **4.5** | 把 `LoadedApk.mApplication` 改成 guest Application | `patchLoadedApkApplication(...)` | 否则 framework 里拿到的还是 stub Application |
| **5** | **在 `Application.onCreate` 之前装 ContentProviders** | `initContentProviders(...)` | **AOSP 语义：Provider 先于 App.onCreate** |
| **6** | 调 `Application.onCreate()` | `callApplicationOnCreate(...)` | 最后一步 |

**为什么 Step 5 必须在 Step 6 前**：
AOSP 的 `LoadedApk.makeApplication` 里，`ContentProvider.onCreate` 先于 `Application.onCreate`。
很多应用（尤其用 `androidx.startup` / Firebase 的）依赖这个顺序 —— 顺序反了会
**Provider 里拿不到初始化好的 Application 单例**，表现为偶发 NPE 或初始化丢失。

**回归判据**：
- `bindApplication` 事件在 Activity 启动事件（`txn`）之前；
- 有 Provider 的 guest：`initContentProviders` 应在 `callApplicationOnCreate` 之前完成。

### 路径 C'：路径解析三层（按优先级）

| 层 | 手段 | 覆盖 | 观测 |
| --- | --- | --- | --- |
| **L-Java** | `VirtualContext` 重写（26 个方法） | 通过 Context 拿路径的代码（~90%） | `ctx` 事件 |
| **L-native GOT** | `redirect_path()` | 被 patch 的库（大多数 native 代码） | `v142 rules=N` |
| **L-syscall** | 仅日志（`armSeccompDiag`） | 直接 `svc` 的代码（**不拦截**） | 无 |

**回归判据**：Java 侧与 native 侧**必须一致**（同一路径两边算出同一结果）。

### 路径 D：legacy 消息（Android 9 以下 / Service / Receiver）

```
handleMessage
  ├─ LAUNCH_ACTIVITY(100) → handleLegacyLaunchActivity   // legacy 事件
  ├─ CREATE_SERVICE(114)  → handleCreateService          // legacy 事件
  └─ RECEIVER(113)        → handleReceiver               // legacy 事件
```

**回归判据**：跑一个带 Service/Receiver 的 guest，`legacy` 至少出现 1 次。

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
| L4 Java 层文件 API「Phase 1 是空的，`hookFileClass()` 只打日志」 | **已实现** 16 个 `RegisterNatives` 方法（`libcore.io.Linux` 8 个 + `java.io.UnixFileSystem` 8 个），但**默认关闭**：`debug.nextvm.javaIo=0`。完整清单见 `架构-hook实现现状-代码为准.md` |
| 未提本仓库新增的写操作 hook | `mkdir`/`mkdirat`/`unlink`/`unlinkat`/`rename` 已 hook 并走 `redirect_path`（`native-hook.cpp:1390-1447`）；`rmdir` 仍缺 |
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
