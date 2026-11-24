# P0-3 波及范围调查：主进程不装 hook 会影响什么

- 日期：2026-11-24
- 目的：执行 P0-3 前的完整波及面调查（用户要求"检查波及范围，例如是否影响 selfcheck"）

---

## 一、`SelfCheckRunner`：**不存在，不受影响** ✅

```
$ find _ref/ShitJumpingVM -name "*SelfCheck*"
（空）
```

**`SelfCheckRunner` 是 P1-1 的产物，还没做** → **P0-3 不可能影响它**。

**但将来做 P1-1 时要注意**：如果 `SelfCheckRunner` 要在主进程跑（通过 `CmdReceiver`），
它读的文件都是**真实路径**（`files/virtual`），所以 **P0-3 后反而更简单**（不需要豁免）。

---

## 二、主进程里**唯一**依赖 hook 的功能：`AntiDetectionEngine`

### 2.1 19 处 `hidePath`

```
AntiDetectionEngine.kt:240  nativeHookBridge.hidePath(path)
AntiDetectionEngine.kt:244  hidePath("/sbin/.magisk")
AntiDetectionEngine.kt:245  hidePath("/data/adb/magisk")
AntiDetectionEngine.kt:246  hidePath("/data/adb/modules")
AntiDetectionEngine.kt:247  hidePath("/cache/.disable_magisk")
AntiDetectionEngine.kt:258  hidePath("/system/xbin/which")
AntiDetectionEngine.kt:284  hidePath(path)
AntiDetectionEngine.kt:311  hidePath("/proc/tty/drivers")
AntiDetectionEngine.kt:336  hidePath("/data/data/${HOST_PACKAGE}")
AntiDetectionEngine.kt:337  hidePath("/data/app/${HOST_PACKAGE}")
AntiDetectionEngine.kt:340  hidePath("/data/data/${HOST_PACKAGE}/${VIRTUAL_DIR}")
AntiDetectionEngine.kt:362  hidePath("/data/data/$pkg")
AntiDetectionEngine.kt:394  hidePath("/system/framework/XposedBridge.jar")
AntiDetectionEngine.kt:395  hidePath("/data/data/de.robv.android.xposed.installer")
AntiDetectionEngine.kt:396  hidePath("/system/lib/libxposed_art.so")
AntiDetectionEngine.kt:397  hidePath("/system/lib64/libxposed_art.so")
AntiDetectionEngine.kt:398  hidePath("/data/misc/riru")
AntiDetectionEngine.kt:399  hidePath("/data/adb/riru")
AntiDetectionEngine.kt:400  hidePath("/data/adb/lspd")
```

### 2.2 它们由谁触发

```
enableAntiDetection(instanceId, level)  ← 唯一入口
  ├─ hookRootChecks()      → 9 处 hidePath（magisk/xposed/riru）
  ├─ hookEmulatorChecks()  → 2 处
  ├─ hookVirtualEnvChecks() → 3 处（含 HOST_PACKAGE）
  ├─ hookXposedChecks()    → 4 处
  └─ hookStackTraceCleanup()
```

**静态搜索 `enableAntiDetection`：只有定义（L161），无调用点**（未实测确认）

### 2.3 P0-3 后的影响

| 情况 | 影响 |
| --- | --- |
| **若 `enableAntiDetection` 从未被调** | **零影响** |
| **若在 guest 进程被调** | **零影响**（guest 仍装 hook） |
| **若在主进程被调** | ⚠️ `hidePath` 变成"只记录不生效"（`g_hidden_paths` 有值但没有 hook 用它）→ **主进程本来就不需要隐藏自己** → **实际上无害，但还是应该改成 guest 进程调用** |

**结论**：**无论哪种情况，P0-3 都不会破坏功能**，最多是"浪费一处调用"。

---

## 三、`NativeHookBridge.addPathRedirection`：**不受影响**

### 3.1 规则注册的时机

```kotlin
// NativeHookBridge.kt
fun setupAppRedirections(guestPackageName: String, instanceId: String, sandboxDataDir: String) {
    addPathRedirection("/sdcard/", "$virtualSdcardDir/")       // L360
    addPathRedirection("/storage/emulated/0/", ...)            // L361
    ...
    addPathRedirection("/data/data/$guestPkg/", ...)           // L314-334 附近的 5 条
}
```

**`setupAppRedirections` 在 guest 启动时调用**（`VirtualEngine` 里）→ **在 guest 进程** → **仍会执行** ✓

### 3.2 `initNativeHooks` 的两处调用

```
VirtualEngine.kt:280   initialize() → 主进程
VirtualEngine.kt:880   getOrCreateClassLoader() → guest 进程
```

**P0-3 只屏蔽第一处** → **guest 进程仍然装 hook** ✓

**但要注意**：`NativeHookBridge` 是**单例**（`object`? 还是 Hilt 单例？）
- 如果是 **Hilt `@Singleton`** → **每个进程一个实例**（进程隔离）→ 无影响
- 如果是 **Kotlin `object`** → 也是每进程一份
- **所以 `initialized` 标志是进程级的** → 主进程跳过不影响 guest ✓

---

## 四、`CmdReceiver` 的 8 条命令：**全部走真实路径，P0-3 后更简单**

| 命令 | 调用 | 文件访问 |
| --- | --- | --- |
| `INSTALL` | `engine.installApp(path)` | **真实** `/sdcard/Download/x.apk` + 沙箱（都是真实路径） |
| `LAUNCH` | `engine.launchAppByPackage(pkg)` | 无文件读（读内存里的注册表） |
| `LAUNCH_ACTIVITY` | 同上 | 无 |
| `UNINSTALL` | `engine.uninstallAppByPackage(pkg)` | 沙箱（真实路径） |
| `LIST` | `engine.getInstalledApps()` | 无 |
| `COLLECT_OUT` | `sandbox.collectProbeOutput()` | **`context.filesDir`（真实）** |
| `LIST_OUT` | `sandbox.listProbeOutput()` | 同上 |
| `CAT_OUT` | `sandbox.readProbeOutput(...)` | 同上 |

**全部不需要路径重定向** → **P0-3 后它们不再被 hook 干扰** →
**`is_import_read_exempt` 可以整个删掉**（连带删掉它引入的两个越权分支）

---

## 五、波及范围总结

| 项 | 影响 | 结论 |
| --- | --- | --- |
| **`SelfCheckRunner`** | **不存在** | ✅ 不受影响 |
| **`AntiDetectionEngine`（19 处 hidePath）** | 入口 `enableAntiDetection` 静态无调用 | ✅ 不受影响（最多浪费一处调用） |
| **`NativeHookBridge.addPathRedirection`** | 在 guest 进程调用 | ✅ 不受影响 |
| **`CmdReceiver` 8 条命令** | 全走真实路径 | ✅ **变好**（不再被 hook 干扰） |
| **`is_import_read_exempt`** | 只为修"主进程读不到" | ✅ **可以删除**（连带 6 处调用） |
| **guest 进程的路径重定向** | 不受影响（`getOrCreateClassLoader` 仍调 `initNativeHooks`） | ✅ 不变 |

**唯一需要额外验证的**：
> **`NativeHookBridge` 的 `initialized` 标志是否是进程级的**（若是 `object` 或 Hilt `@Singleton`，则是）。

---

## 六、P0-3 的实施细节（三处改动）

```kotlin
// 1. NativeHookBridge.initNativeHooks 开头
fun initNativeHooks(context: android.content.Context? = null): Boolean {
    if (initialized) return true
    // 新增：主进程不装 hook
    val procName = android.app.Application.getProcessName()
    val pkg = (context ?: appContext)?.packageName
    if (pkg != null && procName == pkg) {
        Timber.tag(TAG).i("initNativeHooks skipped: container main process")
        initialized = true
        return false
    }
    ...
}

// 2. 删除 is_import_read_exempt 及其 6 处调用（native-hook.cpp）
// 3. 删除 NativeHookBridge 里给主进程用的 import 豁免逻辑（如有）
```

**验证**：
- 主进程 `pidof` 后 `logcat` 应出现 `"initNativeHooks skipped: container main process"`
- `INSTALL /sdcard/Download/x.apk` **不再需要豁免**（因为主进程没有 hook）
- `COLLECT_OUT` / `LIST_OUT` 仍正常
- guest 启动后 `PLT hooks installed successfully` **仍然出现**（在 guest 进程里）
