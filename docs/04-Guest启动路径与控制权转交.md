# Guest 的启动路径：执行权限如何"转交"，宿主能影响什么

- 调查日期：2026-11-21
- 证据来源：AOSP `ActivityThread` / `Instrumentation` 公开语义 + 本地克隆的两份实现
  - NestSpace：`_ref/NestSpace/app/src/main/java/com/coke/nestspace/container/`
  - NEXTVM（ShitJumpingVM 上游）：`_ref/NEXTVM/core/virtualization/`、`core/sandbox/`

---

## 0. 先回答最关键的一句：**没有"权限转交"这回事**

| 层面 | 是否发生"转交" | 真相 |
| --- | --- | --- |
| Linux 内核（UID/GID） | ❌ | guest 跑在**宿主进程里**，UID 就是宿主 UID，内核层面没有任何身份变化 |
| SELinux 域 | ❌ | 仍是 `u:rw:untrusted_app:s0:cX,cY`（宿主自己的 categories） |
| capability / 权限集 | ❌ | 完全继承宿主进程的能力上限 |
| Java 层控制权 | ✅ | 通过 **ClassLoader / Context / Instrumentation** 三个对象把执行流交给 guest 代码 |
| Binder 调用身份 | ⚠️ 部分 | 内核附加的 `callingUid` **不可改**；Parcel 里的 `callingPackage` / `AttributionSource` **可改** |
| Android 运行时权限 | ⚠️ 代理 | 系统权限控制器只认已安装包，容器必须自己代答（见 §4） |

> **准确表述：容器做的不是"把权限转交给 guest"，而是"在自己的权限域内，把 Java 层的执行入口交给 guest 代码"。**
> 这也解释了为什么"权限伞"不可避免：guest 自动获得宿主已授予的全部权限，而宿主没申请的权限 guest 也拿不到。

---

## 1. 系统侧原始启动链（容器要"寄生"的那条链）

```
Launcher/通知/URL
  → Instrumentation.execStartActivity()            (发起方进程)
  → IActivityTaskManager.startActivity()            (Binder → system_server)
  → ATMS/ActivityStarter → ActivityTaskSupervisor
      → 目标进程不存在 → ZygoteProcess → fork
  → 新进程 ActivityThread.main()
      → Looper.prepareMainLooper()
      → attach() → IActivityManager.attachApplication(mAppThread)
  → system_server: attachApplicationLocked()
      → bindApplication(...)                        (先绑定 Application)
      → realStartActivityLocked() → app.thread.scheduleLaunchApplication()
  → 应用进程 ApplicationThread.scheduleLaunchActivity()
      → sendMessage(H.LAUNCH_ACTIVITY)              (Android 8 及以前)
      → sendMessage(H.EXECUTE_TRANSACTION = 159)    (Android 9+：所有 Activity 事务统一走这里)
  → TransactionExecutor.execute(ClientTransaction)
      → LaunchActivityItem.execute()
      → ActivityThread.handleLaunchActivity()
          → performLaunchActivity():
              aInfo = r.activityInfo
              appContext = createBaseContextForActivity(r)
              activity = mInstrumentation.newActivity(cl, className, intent)   ← ★ 实例化的唯一入口
              activity.attach(appContext, this, mInstrumentation, token, ..., info, ...)  ← ★ Instrumentation 被注入
              mInstrumentation.callActivityOnCreate(activity, r.state)          ← ★ 生命周期回调
```

**四个"钩子位置"由 AOSP 自身结构决定，不是黑科技：**

| 位置 | 谁在这里生效 | 容器能改什么 |
| --- | --- | --- |
| `ActivityThread.mInstrumentation`（字段） | 进程内"Activity 工厂 + 回调总闸" | 整个替换 ⇒ 所有 newActivity / 生命周期回调都过容器 |
| `H.EXECUTE_TRANSACTION(159)`（消息） | Android 9+ 所有 Activity 事务入口 | 改 `LaunchActivityItem.mInfo`(ActivityInfo) 与 Intent |
| `LoadedApk.mClassLoader`（字段） | `newActivity(cl, ...)` 里的 `cl` 来源 | 换成 guest 的 `DexClassLoader` |
| `ApplicationInfo` / `ContextImpl` 构造入参 | 路径、包名、`nativeLibraryDir` 的事实源 | 换成虚拟环境快照 |

---

## 2. 两条实际被采用的介入路线（本地代码证据）

### 路线 A：替换 `ActivityThread.mInstrumentation`（NestSpace）

```
ContainerSessions.kt:118        →  threadClass.getDeclaredField("mInstrumentation")   // 反射替换
ContainerHostInstrumentation.kt →  override fun newActivity(cl, className, intent)
                                    ├─ Class.forName(className, false, cl)
                                    ├─ 判断是不是宿主 StageAActivity
                                    └─ 宿主 StageA → 用宿主 ClassLoader；否则 → 用传入的 guest ClassLoader
ContainerInstrumentation.kt:29  →  交给 guest Activity 的 Instrumentation（见路线 C）
StageAActivity.kt:55            →  manifest 里预注册的占位 Activity
```

关键点：

- **一个点控制全部 Activity**：`mInstrumentation` 被换掉后，`newActivity` / `callActivityOnCreate` / `execStartActivity` 全部经过容器；
- 有一段**双 Instrumentation** 设计值得抄：
  - `ContainerHostInstrumentation`（替换到 `ActivityThread`）负责"宿主占位 Activity 与 guest Activity 的分流"；
  - `ContainerInstrumentation`（通过 `Activity.attach(..., instr, ...)` 注入给 guest Activity）负责"guest 内部的 `startActivityForResult` 与权限请求拦截"；
  - 注释原文（`ContainerInstrumentation.kt:14-16`）：`Activity.startActivityForResult` 调 `mInstrumentation.execStartActivity(...)`，"that method is public in AOSP but hidden from the SDK stubs. We declare the exact AOSP signatures without `override`"。
  - 即：**用"声明同名同签名但不写 override"的方式绕过了"隐藏 API 不能 override"的限制**，这是一条很实用的工程技巧。

### 路线 B：拦截 `H.EXECUTE_TRANSACTION = 159`（NEXTVM / ShitJumpingVM）

```
ActivityThreadHook.kt:23   →  "EXECUTE_TRANSACTION = 159 (Android 9+): ALL activity lifecycle goes through this"
ActivityThreadHook.kt:340  →  1. Swap ActivityInfo.name + applicationInfo to guest app
                              2. Replace ClassLoader via LoadedApk so performLaunchActivity() uses DexClassLoader
ActivityThreadHook.kt:422  →  nameField?.set(activityInfo, targetActivity)     // ActivityInfo.name → guest 类名
ActivityThreadHook.kt:427  →  aiPkgField?.set(activityInfo, targetPackage)     // ActivityInfo.packageName → guest 包名
```

对比：

| | 路线 A（换 Instrumentation） | 路线 B（拦 EXECUTE_TRANSACTION） |
| --- | --- | --- |
| 介入层级 | Java 对象 | Handler 消息 |
| 覆盖面 | 所有 Activity 生命周期 + startActivity | 所有 Activity 事务（含 9+ 的 lifecycle item） |
| 粒度 | 粗（一个总闸） | 细（可逐 item 改 Intent/ActivityInfo） |
| 版本风险 | `mInstrumentation` 字段名、API 差异 | **消息码 159 是硬编码常量**，跨版本需验证 |
| 额外成本 | 需处理"宿主自己也要用 Instrumentation" | 需替换 `ActivityThread.mH` 的 Callback 或包一层 Handler |
| 适合 | 简单、单一入口 | 需要精细改写（如多 launchMode、多 task） |

### 路线 C：注入 `LoadedApk.mClassLoader`（NEXTVM 的 `VirtualClassLoaderInjector`，244 行）

三级策略（`VirtualClassLoaderInjector.kt:8-52`）：

1. **直接改**：在 `ActivityThread.mPackages[guestPkg]` 找到 `LoadedApk`，写 `mClassLoader`；
2. **克隆宿主 LoadedApk**：以 guest 包名注册一个新的 `LoadedApk` 副本，再写入 guest ClassLoader；
3. **Unsafe 兜底**：标准反射被阻断时直接写字段。

注释里还有一条重要版本事实（`:15`）：

> *"Previous approach used `Resources.setClassLoader()` which doesn't exist on API 34+."*

⇒ **Android 14 起 `Resources.setClassLoader()` 已不可用**，必须直接改 `LoadedApk.mClassLoader`。这是我们支持 14/15/16 时必须知道的细节。

---

## 3. "执行流"逐段交接表（谁在什么时候握住控制权）

| 阶段 | 控制权在谁 | 容器做了什么 | guest 何时获得执行 |
| --- | --- | --- | --- |
| 用户点击 | 宿主 | 读虚拟包注册表，决定实例与槽位 | — |
| 启动占位组件 | 系统（真实创建 StageA） | 选一个 `StageAActivity` 变体（按 launchMode 区分） | — |
| `ActivityThread.handleLaunchActivity` | 系统 | 保证 `mInstrumentation` 已被替换 | — |
| `Instrumentation.newActivity` | **容器** | 判定"宿主 StageA 还是 guest"，选 ClassLoader 与 className | — |
| 实例化 | ART | `cl.loadClass(guestActivity).newInstance()` | ★ **guest 构造函数执行**（第一个 guest 代码执行点） |
| `Activity.attach` | **容器** | 注入 guest `Context`（虚拟路径/包名）与 `ContainerInstrumentation` | guest 拿到 `baseContext` |
| `callActivityOnCreate` | **容器** | 顺序回调；可选在前后插入兼容逻辑 | ★ `guest.onCreate()` |
| guest 内部 `setContentView` / 资源 | 系统 + 容器 | Resources/AssetManager 已绑定 guest APK | guest 用自己的资源 |
| guest `startActivity` | **容器** | `execStartActivity` 拦截 → 转成新的 StageA 启动 | — |
| guest 发起 Binder | 系统 | 仅能改 Parcel 里的 callingPackage/AttributionSource | ★ IPC 真实发出 |
| guest 申请运行时权限 | **容器** | 拦 permission-controller Intent，自行用宿主身份弹窗 | 回填 `onRequestPermissionsResult` |

**注意"★"三处**：guest 代码真正获得执行权只有三个时刻——**构造函数、`onCreate` 起的所有生命周期回调、以及它自己发起的 Binder 调用**。除此之外的所有环节都在容器手里。

---

## 4. 运行时权限：为什么容器必须"自己当权限控制器"

NestSpace `ContainerInstrumentation.kt` 的注释把机制说得很清楚（原文）：

> *"Runtime permission requests are generated by the framework as an Intent to the permission controller. **The controller cannot grant permissions to a package that is not installed**, so the request is answered by ContainerPermissions instead (real host dialog where possible)."*

展开：

1. `Activity.requestPermissions()` 在框架内部会构造一个指向**权限控制器**（`com.google.android.permissioncontroller` / `com.android.permissioncontroller`）的 Intent；
2. 该控制器的 Binder 侧会用 `PackageManager` 校验"这个包是否已安装、是否声明了该权限"——**虚拟包天然不满足**，直接失败；
3. 所以容器必须在 `execStartActivity` 里识别"这是权限请求"，改为：
   - 用**宿主身份**弹真实系统权限对话框（确保系统真的授权给宿主 UID，否则底层仍不可用）；
   - 把结果写入虚拟权限表，并回调 guest 的 `onRequestPermissionsResult`。
4. `checkSelfPermission` 则必须被代理：返回"虚拟权限表"的结果，而**不是**系统真实结果。

**由此得到两条工程约束：**

- 宿主 `AndroidManifest` 必须**预先声明** guest 可能用到的所有权限（否则真实对话框无从弹起，底层也无法通过 SELinux/AppOps）。这就是"权限伞"的来源，也是"宿主 manifest 权限越少越干净"与"guest 需要权限"之间的直接冲突。
- `MANAGE_EXTERNAL_STORAGE`、`QUERY_ALL_PACKAGES` 这类高危权限不能为了兼容 guest 而滥申请（Play 政策 + 风控特征），必须走"按需声明"。

---

## 5. 宿主能影响什么：干预点全景

| 维度 | 可干预内容 | 不可干预 |
| --- | --- | --- |
| 进程 | 选哪个槽位、进程名、PID 视图、崩溃后重建 | 真实 UID、SELinux 域、进程的 fork 来源 |
| 代码 | ClassLoader、dex/oat 来源、native lib 目录、插件 APK 挂载 | ART 内部编译策略（只能通过路径与文件影响） |
| 资源 | Resources/AssetManager、主题、语言、密度 | 系统级资源配置 |
| 身份 | 包名、`observedUid`、Android ID/DID、`Build` 快照 | 真实 UID、真实安装状态 |
| 路径 | `Context`/`ApplicationInfo` 视图 + native 前缀映射（见 `03`） | 内核 inode、`/proc/self/maps` 的物理映射 |
| 组件 | Activity 栈/Intent/launchMode、Service、Broadcast、Provider、Job | `system_server` 的组件注册表 |
| 系统服务 | Binder 代理返回值（PM/AM/Account/Telephony/WebView…） | 服务端对 `callingUid` 的判断 |
| 权限 | `checkSelfPermission` 返回值、`requestPermissions` 流程 | 真实授权的判定（必须宿主真的被授予） |
| 生命周期 | Application/Provider 启动顺序、首帧时序、`onResume` 时机 | 主线程 Looper 的真实消息顺序 |
| 崩溃 | 主线程致命异常隔离、只重启该槽位 | 真实 SIGSEGV 的内核行为 |
| native | 目标模块 GOT/PLT、`/proc`/property 视图（可选） | 系统库、直接 `syscall`、内联 `svc` |

---

## 6. 版本敏感点（我们目标 Android 14/15/16 必须逐条验证）

| 项 | 风险 | 验证方式 |
| --- | --- | --- |
| `ActivityThread.mInstrumentation` 字段 | 私有字段，可能改名 | 启动探针：替换后能否拦截 `newActivity` |
| `ActivityThread.mH` / `EXECUTE_TRANSACTION = 159` | 常量硬编码 | 探针：能否收到 159 消息并读到 `LaunchActivityItem.mInfo` |
| `LoadedApk.mClassLoader` | 私有字段 | 探针：注入后 `newActivity(cl,...)` 拿到 guest 类 |
| `Resources.setClassLoader()` | **API 34+ 不存在**（NEXTVM 注释实证） | 禁止使用该路径 |
| non-SDK 限制 | targetSdk 36 收紧 | 统一走 `hiddenapibypass`；反射失败必须降级为可诊断错误 |
| Android 16 忽略 orientation/resizability | guest 的 `screenOrientation` 失效 | 容器自己实现方向策略 |
| Android 14 DCL 只读 | 动态加载的 APK/DEX 必须只读 | 所有加载源 `MODE_READONLY` |
| 16 KB page size | 自研 native 库与 guest SO 都要对齐 | 真机 + 16KB 模拟器各验一次 |

---

## 7. 给实现的最小启动链（P0 清单）

按依赖顺序，做到第 6 步就能看到 guest 首帧：

1. **占位组件池**：manifest 预注册 `StageAActivity`（按 launchMode 分几种）+ 若干 Service/Provider/Receiver 槽位
2. **ClassLoader**：为 guest 建 `DexClassLoader`（APK 只读），并把 `LoadedApk.mClassLoader` 指过去（三级策略）
3. **Instrumentation**：替换 `ActivityThread.mInstrumentation` = `HostInstrumentation`（分流用）；给 guest Activity 注入 `ContainerInstrumentation`（拦截用）
4. **Context**：`ContainerContext`（虚拟 `getPackageName` / `getApplicationInfo` / 数据目录 / `getSystemService` 代理）
5. **Application 启动**：`mInstrumentation.newApplication(cl, appClass, context)` → `attachBaseContext` → `onCreate`
6. **首 Activity**：`startActivity(StageA)` → `newActivity` 分流 → `attach` → `onCreate`
7. （之后）权限代理、Binder 代理、多进程槽位、崩溃隔离

**验收信号（不靠"看起来出来了"）**：
`startup phase=CLASS_LOADER → APPLICATION_CREATE → FIRST_ACTIVITY_OPENED` 全部有真实耗时日志，且 `EnvironmentConsistencyProbe` 的包名/UID/dataDir/APK 路径四项一致。

---

## 8. 一句话结论

> **"执行权限的转交"在技术上不存在——guest 与宿主共享同一 UID、同一 SELinux 域、同一权限集。**
> 容器真正做的是：在系统给它的那条 Activity 启动链上，用 **`mInstrumentation`（或 `EXECUTE_TRANSACTION`）+ `LoadedApk.mClassLoader` + `Activity.attach` 的 Context** 三个交接点，把"实例化谁、用哪套资源与路径、按什么顺序回调"的决定权拿到手里，然后把执行流交给 guest。
>
> 因此**宿主能影响的范围精确地等于"Java 对象与 Parcel 内容"**；而 UID、SELinux、内核 Binder 归因、以及"权限是否真的被系统授予"这四件事，永远在容器之外。
