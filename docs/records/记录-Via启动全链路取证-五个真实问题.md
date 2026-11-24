# Via 启动全链路取证（16:19 那次）：五个真实问题

- 日期：2026-11-24 16:19
- 方法：`LAUNCH mark.via` → 等 18s → 抓全量 logcat → 按 pid 28055 + 时间戳过滤
- 结论：**`bindViaFrameworkLoadedApk` 必然失败的原因彻底查清**，并额外发现 4 个独立问题

---

## 一、核心证据：`:p1`（guest 进程）里先跑的是容器的 `NextVmApplication`

```
16:19:00.514 28055 28055 I NextVmApplication: NEXTVM Application started — Android 35
16:19:00.514 28055 28055 W m.nextvm.app:p1: Unsupported class loader
16:19:00.529 28055 28055 W m.nextvm.app:p1: Unsupported class loader
16:19:00.543 28055 29450 I NextVmApplication$onCreate: Initializing VirtualEngine...
16:19:00.853 28055 28055 E LoadedApk: App instance already created for package=com.nextvm.app
                                        instance=com.nextvm.app.NextVmApplication@ada75ed
```

### 因果链（彻底查清）

```
1. 启动 stub Activity（P1$Standard00，android:process=":p1"）
2. :p1 进程 fork → 按 manifest，:p1 的组件属于 com.nextvm.app
   → Android 用容器的 ApplicationInfo 创建 Application
     → NextVmApplication 被实例化并 onCreate
       → LoadedApk.mApplication = <NextVmApplication>       ← 赋值已完成
         → NextVmApplication.onCreate 里初始化 VirtualEngine

3. VirtualEngine.ensureGuestApplicationBooted（被 ActivityThreadHook 调用）
   → VirtualApplicationManager.bindApplication
     → bindViaFrameworkLoadedApk
       → loadedApk.makeApplication(false, null)              ← L1446
         → AOSP: if (mApplication != null) { Log.wtf(...); return mApplication; }
           → 返回**容器的** NextVmApplication，并抛 Log.wtf
             → bindViaFrameworkLoadedApk 拿到的是容器 App
               → 后续 guest 逻辑全跑在容器 Application 上
```

**所以这不是"偶发失败"，而是"架构上必然"**：
> **`:p1` 的 `LoadedApk.mApplication` 在 `ActivityThreadHook` 介入之前就已经被赋值为 `NextVmApplication`。**

---

## 二、额外发现的 4 个真实问题

### 问题 2：`startApkWatchdog` 的 `link()` 失败（`EACCES`）

```
16:19:00.842 28055 29453 E ATHook: Safe execution failed
16:19:00.842 28055 29453 E ATHook: android.system.ErrnoException: link failed: EACCES (Permission denied)
    at libcore.io.Linux.link(Native Method)
    at libcore.io.ForwardingOs.link(ForwardingOs.java:454)
    at libcore.io.BlockGuardOs.link(BlockGuardOs.java:237)
    at android.system.Os.link(Os.java:424)
    at ActivityThreadHook.startApkWatchdog$lambda$18$lambda$17(ActivityThreadHook.kt:839)
```

**含义**：`startApkWatchdog` 试图用硬链接（`link`）保住 APK 副本，**但被拒**。

**可能原因**：
- 源和目标不在同一个文件系统（`link` 要求同一 fs）
- 或者目标目录权限不足
- 或者 SELinux 拒绝

**影响**：如果某些 guest SDK 会删自己的 APK（注释提到 `ZWRisk.setup()`），**watchdog 保不住** → guest 可能崩溃。

### 问题 3：`seccomp USER_NOTIF` 安装失败

```
16:19:00.849 28055 28055 E NextVM-Native: seccomp-diag: USER_NOTIF install FAILED (errno=22 flags=0x9)
```

**`errno=22` = `EINVAL`** → **seccomp 的 `USER_NOTIF` 在这台设备上不支持**（或 flags 组合不合法）。

**含义**：容器尝试用 seccomp 拦截系统调用（诊断目的），**在 Android 15 / vivo 上装不上**。

**这也解释了为什么 `hooked_syscall` 只打日志** —— seccomp 这条路走不通，只能靠 GOT hook，而 GOT hook 对 `syscall` 又拦不住直接调用。

### 问题 4：`Unsupported class loader` × 2

```
16:19:00.514 W m.nextvm.app:p1: Unsupported class loader
16:19:00.529 W m.nextvm.app:p1: Unsupported class loader
```

**含义**：某个 API 收到了不支持的 ClassLoader 类型（可能是 `DexClassLoader` 的子类，或者被容器替换过的 loader）。

**这条警告在每次启动都出现**，但没有立即导致崩溃。

### 问题 5：`SvcProxyMgr` 的反射字段过时

```
16:19:00.571 W SvcProxyMgr: Field mAudioService not found on android.media.AudioManager
```

**含义**：`SystemServiceProxyManager` 反射 `AudioManager.mAudioService` 失败 ——
**Android 15 已经把这个字段改名或移除**。

**这是"过时"最直接的证据之一**（用户怀疑的方向）。

---

## 三、按"严重性"排序

| # | 问题 | 严重性 | 是否阻塞 Via 主页 |
| --- | --- | --- | --- |
| **1** | **`bindViaFrameworkLoadedApk` 必然失败**（`mApplication` 已存在） | **高** | ⚠️ **可能**（guest Application 没绑上） |
| **2** | `startApkWatchdog` 的 `link()` 失败 | 中 | ❌ 不直接 |
| 3 | `seccomp USER_NOTIF` 装不上 | 低（本来就只是诊断） | ❌ |
| 4 | `Unsupported class loader` | 中 | ⚠️ 可能 |
| 5 | `mAudioService` 反射过时 | 低 | ❌ |

---

## 四、但 Via 界面确实起来了

```
16:19:00.540 I ATHook  : ActivityThread.mH hook installed successfully
16:19:00.540 I ATHook  : Main looper crash guard installed
16:19:00.541 I BinderWrap: [activity] binder wrap installed: codes start=14 asUser=139
16:19:00.541 I BinderWrap: [activity_task] binder wrap installed: codes start=1 asUser=3
```

**`ActivityThreadHook` 装成功了** → Via 的 Activity 能起来（截图证明界面在）。

**所以问题不是"起不来"，而是"跑在容器的 Application 上"** ——
**guest 的 Application（`mark.via.ViaApp` 之类）根本没被创建**。

**这解释了什么**：
- Via 的**静态初始化、单例、配置**全部没执行
- 所以 Via 可能**退化成"最小功能模式"**
- **主页显示源码**可能就是"WebView 初始化配置没跑"的后果

---

## 五、下一步的三个方向

| # | 方向 | 说明 |
| --- | --- | --- |
| **A** | **让 `:p1` 不创建容器的 `NextVmApplication`** | 治本。但 manifest 里 `:p1` 的组件是容器的，Android 强制用容器 ApplicationInfo → **需要改架构**（如把 stub 组件放到独立的 `:guest` 进程，并在那里用 `LoadedApk` 替换） |
| **B** | **绕过 `makeApplication`，自己 new guest Application** | 直接用 guest ClassLoader 反射创建 `Application` 实例，手动 `attach(context)`，不经过 `LoadedApk` |
| **C** | **接受"guest Application 不跑"，补齐 Via 需要的最小初始化** | 治标，且要为每个应用定制 → 不可维护 |

**B 最现实**：`bindViaFrameworkLoadedApk` 已经有 `createApplication` 的 legacy 路径（L536），
**只是被 framework path 抢先了** → **加一个"`mApplication` 已存在就跳过 framework path"的判断** 即可。

---

## 六、一条可以立即验证的推论

**如果问题 1 是 Via 主页异常的根因**，那么：

```
在 bindApplication 里加：
  如果 LoadedApk.mApplication != null 且不是 guest 的类
    → 直接跳过 framework path，走 legacy createApplication
```

**验证**：改完后 Via 的 `getApplication()` 应该返回 `mark.via.*App` 而不是 `NextVmApplication`。

**这个验证不需要懂 WebView** —— 它只测"guest Application 绑没绑上"。
