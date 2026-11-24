# 修改计划：SettingsStore + CmdReceiver 开关接口 + SELFCHECK + AntiDetectionEngine 缺陷修复

- 日期：2026-11-24
- 用户要求：执行任务 1（SettingsStore + CmdReceiver + UI 开关）、2（SELFCHECK 命令）、4（修 `is_path_hidden` 前缀匹配 + `readdir` 过滤），并更新测试
- 明确不做：**"第二次启动闪退"是已修复的古早 bug，不尝试复现验证**

---

## 任务 4（先做）：`AntiDetectionEngine` 缺陷 1+2

### 4.1 缺陷 1：`is_path_hidden` 精确匹配 → 前缀匹配

**现状**：
```cpp
// native-hook.cpp:243
static bool is_path_hidden(const char* path) {
    if (path == nullptr || g_hidden_paths.empty()) return false;
    return g_hidden_paths.count(std::string(path)) > 0;   // ← 精确匹配
}
```

**问题**：`hidePath("/data/data/com.nextvm.app")` **只隐藏这一个字符串**，
`/data/data/com.nextvm.app/files`、`/data/data/com.nextvm.app/cache` 全部可见。

**改法**：
```cpp
static bool is_path_hidden(const char* path) {
    if (path == nullptr || g_hidden_paths.empty()) return false;
    // /proc 有独立的 spoof 机制，不参与 hidePath（否则会破坏 /proc/self/* 的伪造）
    if (strncmp(path, "/proc", 5) == 0) return false;

    std::string p(path);
    // 归一化：折叠重复斜杠、去掉尾部斜杠
    std::string norm;
    norm.reserve(p.size());
    for (char c : p) {
        if (c == '/' && !norm.empty() && norm.back() == '/') continue;
        norm.push_back(c);
    }
    while (norm.size() > 1 && norm.back() == '/') norm.pop_back();

    for (const auto& hidden : g_hidden_paths) {
        if (hidden.empty()) continue;
        // 精确相等
        if (norm == hidden) return true;
        // 前缀匹配：必须是 "hidden + /"（避免 /data/data/com.nextvm.app2 被误判）
        if (norm.size() > hidden.size() &&
            norm.compare(0, hidden.size(), hidden) == 0 &&
            norm[hidden.size()] == '/') {
            return true;
        }
    }
    return false;
}
```

**关键设计**：
- **`/proc` 排除**（不参与 hidePath，避免破坏 spoof）
- **路径归一化**（折叠 `//`、去尾斜杠）
- **前缀必须后跟 `/`**（避免 `com.nextvm.app2` 误判）

### 4.2 缺陷 2：`readdir` 过滤（包装 `DIR*`）

**现状**：`hooked_opendir` 只对 `/proc` 打日志，不修改返回结果。

**问题**：`opendir("/data/data")` + `readdir` 能看到 `com.nextvm.app` → **与缺陷 1 自相矛盾**。

**改法**：

```cpp
// 1) 维护 DIR* -> 是否过滤 的映射（线程安全）
static std::mutex g_dir_filter_mutex;
static std::unordered_map<DIR*, bool> g_dir_filter_enabled;

// 2) hooked_opendir 打开后决定是否过滤
static DIR* hooked_opendir(const char* path) {
    // ... 原有的 /proc 日志 ...
    DIR* d = real_opendir(actual_path);
    if (d != nullptr) {
        std::string norm = normalize_path(actual_path);
        bool filter = should_filter_dir(norm);   // 目录本身是隐藏路径的祖先
        std::lock_guard<std::mutex> lk(g_dir_filter_mutex);
        g_dir_filter_enabled[d] = filter;
    }
    return d;
}

// 3) hooked_readdir 跳过隐藏项
static struct dirent* hooked_readdir(DIR* dirp) {
    for (;;) {
        struct dirent* e = real_readdir(dirp);
        if (e == nullptr) {
            // 关闭时清理
            std::lock_guard<std::mutex> lk(g_dir_filter_mutex);
            g_dir_filter_enabled.erase(dirp);
            return nullptr;
        }
        bool filter = false;
        {
            std::lock_guard<std::mutex> lk(g_dir_filter_mutex);
            auto it = g_dir_filter_enabled.find(dirp);
            filter = (it != g_dir_filter_enabled.end() && it->second);
        }
        if (!filter) return e;
        // 拼接完整路径后判断
        std::string full = dir_prefix_of(dirp) + "/" + e->d_name;
        if (!is_path_hidden(full.c_str())) return e;
        // 被隐藏 -> 继续下一个
    }
}

// 4) hooked_closedir 清理
static int hooked_closedir(DIR* dirp) {
    {
        std::lock_guard<std::mutex> lk(g_dir_filter_mutex);
        g_dir_filter_enabled.erase(dirp);
    }
    return real_closedir(dirp);
}
```

**关键设计**：
- **`DIR*` 无法从 glibc 拿到路径** → 需要**在 `opendir` 时记录 `DIR* -> 目录路径`**
- **`readdir` 时拼接 `dir + "/" + d_name`** 再判断
- **`closedir` 清理**（避免 map 泄漏）
- **`readdir64` 也要 hook**（现代代码用 64 位版本）

**新增 hook 表项**：`readdir`、`readdir64`、`closedir`

### 4.3 风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | `readdir` 是高频调用（游戏引擎会遍历大量目录） | 只在 `filter=true` 的 DIR* 上做拼接；其余直接返回 |
| 2 | `DIR*` map 泄漏（忘记 closedir） | 用 `__attribute__((destructor))` 兜底清理；或用 LRU 上限 |
| 3 | **主进程不装 hook** → 容器自己遍历沙箱不受影响 | ✅ P0-3 已保证 |
| 4 | `is_path_hidden` 前缀匹配可能误伤 | 前缀必须后跟 `/`（已设计） |

---

## 任务 1：`SettingsStore` + CmdReceiver + UI 开关

### 1.1 新建 `core:common/SettingsStore.kt`

```kotlin
package com.nextvm.core.common

import android.content.Context
import android.content.SharedPreferences
import timber.log.Timber

/**
 * 统一设置存储。UI（SettingsViewModel）与 CLI（CmdReceiver）共用一份状态。
 *
 * 每个 key 都有三个属性：
 *   - default: 默认值（= 当前实际行为）
 *   - real:    真实实现状态 -> "enforced" / "not-implemented" / "partial" / "implemented-but-unwired"
 *   - value:   当前值（持久化在 SharedPreferences）
 */
@Singleton
class SettingsStore @Inject constructor(@ApplicationContext private val context: Context) {

    enum class RealState { ENFORCED, NOT_IMPLEMENTED, PARTIAL, IMPLEMENTED_BUT_UNWIRED }

    data class Entry<T>(
        val key: String,
        val default: T,
        val real: RealState,
        val note: String = ""
    )

    private val prefs: SharedPreferences by lazy {
        context.getSharedPreferences("nextvm_settings", Context.MODE_PRIVATE)
    }

    val entries: List<Entry<*>> = listOf(
        Entry("fileIsolation",        true,       RealState.ENFORCED,              "native hook 一直生效"),
        Entry("permissionFirewall",   false,      RealState.NOT_IMPLEMENTED,       "P0-4 还没做"),
        Entry("verboseLogging",       false,      RealState.PARTIAL,               "debug 包 LOGD 一直可见"),
        Entry("antiDetection",        false,      RealState.IMPLEMENTED_BUT_UNWIRED, "AntiDetectionEngine 零调用"),
        Entry("antiDetectionLevel",   "MODERATE", RealState.ENFORCED,              "BASIC/MODERATE/AGGRESSIVE")
    )

    fun getBoolean(key: String): Boolean =
        prefs.getBoolean(key, entries.firstOrNull { it.key == key }?.default as? Boolean ?: false)

    fun getString(key: String): String =
        prefs.getString(key, entries.firstOrNull { it.key == key }?.default as? String ?: "") ?: ""

    /** @return null 表示成功，否则是错误信息 */
    fun set(key: String, rawValue: String): String? {
        val entry = entries.firstOrNull { it.key == key }
            ?: return "unknown key: $key (known: ${entries.joinToString { it.key }})"
        return when (entry.default) {
            is Boolean -> {
                val v = rawValue.lowercase()
                if (v !in listOf("true", "false", "1", "0")) return "value must be true/false"
                prefs.edit().putBoolean(key, v == "true" || v == "1").apply()
                Timber.tag(TAG).i("SETTINGS %s=%s (real=%s)", key, v, entry.real)
                null
            }
            is String -> {
                if (key == "antiDetectionLevel" &&
                    rawValue.uppercase() !in listOf("BASIC", "MODERATE", "AGGRESSIVE")) {
                    return "value must be BASIC/MODERATE/AGGRESSIVE"
                }
                prefs.edit().putString(key, rawValue).apply()
                Timber.tag(TAG).i("SETTINGS %s=%s (real=%s)", key, rawValue, entry.real)
                null
            }
            else -> "unsupported type for $key"
        }
    }

    /** 供 CmdReceiver 输出 */
    fun dumpAll(): List<String> = entries.map { e ->
        val cur = when (e.default) {
            is Boolean -> getBoolean(e.key).toString()
            is String -> getString(e.key)
            else -> "?"
        }
        "SETTINGS key=${e.key} value=$cur (default=${e.default}, real=${e.real.name.lowercase().replace('_', '-')}" +
            (if (e.note.isNotEmpty()) ", note=${e.note}" else "") + ")"
    }

    companion object { private const val TAG = "SettingsStore" }
}
```

### 1.2 `CmdReceiver` 加两条命令

```kotlin
// 常量
const val ACTION_SETTINGS_GET = "com.nextvm.cmd.SETTINGS_GET"
const val ACTION_SETTINGS_SET = "com.nextvm.cmd.SETTINGS_SET"

// EntryPoint 加 settings()
fun settings(): SettingsStore

// onReceive
ACTION_SETTINGS_GET -> {
    val store = settings()
    store.dumpAll().forEach { Log.i(TAG, it) }
    Log.i(TAG, "SETTINGS_GET done count=${store.entries.size}")
}
ACTION_SETTINGS_SET -> {
    val key = intent.getStringExtra("key")
    val value = intent.getStringExtra("value")
    if (key.isNullOrBlank() || value == null) {
        Log.w(TAG, "SETTINGS_SET: missing --es key / --es value")
        return
    }
    val err = settings().set(key, value)
    if (err == null) Log.i(TAG, "SETTINGS_SET ok: $key=$value")
    else Log.e(TAG, "SETTINGS_SET failed: $err")
}
```

### 1.3 `SettingsViewModel` 接上 `SettingsStore`

```kotlin
// 三个 toggle 改为
private fun toggleFileIsolation() {
    val cur = settingsStore.getBoolean("fileIsolation")
    val err = settingsStore.set("fileIsolation", (!cur).toString())
    _uiState.update { it.copy(fileIsolationEnabled = settingsStore.getBoolean("fileIsolation")) }
    // 提示里带上 real 状态
    val real = settingsStore.entries.first { it.key == "fileIsolation" }.real
    emit(ShowMessage("File isolation=$cur->${!cur} (real=$real)"))
}
// 同理 permissionFirewall / verboseLogging

// loadSettings 里从 store 读初值
_uiState.update { it.copy(
    fileIsolationEnabled = settingsStore.getBoolean("fileIsolation"),
    permissionFirewallEnabled = settingsStore.getBoolean("permissionFirewall"),
    verboseLogging = settingsStore.getBoolean("verboseLogging"),
) }
```

**关键**：**`Permission Firewall` 的 `real = NOT_IMPLEMENTED`** → **按用户要求"功能不明暂不实现实际功能"**，
只在提示里明确告知"已保存，但暂未实现"。

---

## 任务 2：`SELFCHECK` 命令 + `:selfcheck` 进程

### 2.1 manifest 加 Service

```xml
<service
    android:name=".selfcheck.SelfCheckService"
    android:process=":selfcheck"
    android:exported="false" />
```

### 2.2 `SelfCheckService`（新文件）

```kotlin
class SelfCheckService : Service() {
    override fun onBind(intent: Intent?) = null
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Thread {
            val report = runChecks()
            val out = File(File(filesDir.parentFile, "probe-out"), "selfcheck.json")
            out.parentFile?.mkdirs()
            out.writeText(report)
            Log.i(TAG, "SELFCHECK done pid=${Process.myPid()} " +
                "proc=${Application.getProcessNameCompat()} -> ${out.absolutePath} (${out.length()} B)")
            stopSelf()
        }.start()
        return START_NOT_STICKY
    }

    private fun runChecks(): String { /* 9 项，见下 */ }
}
```

### 2.3 9 项检查

| # | 检查 | 判据 |
| --- | --- | --- |
| 1 | 进程名 | 应为 `<pkg>:selfcheck` |
| 2 | `initNativeHooks` 是否装了 | 需要 `NativeHookBridge.isNativeAvailable()` |
| 3 | `g_hidden_paths` 数量 | `getHiddenPathCount()` |
| 4 | `g_path_redirects` 数量 | `getRedirectionCount()` |
| 5 | **双轨沙箱是否收敛** | `SandboxManager` 的根 == `File(filesDir, "virtual")` |
| 6 | **`app_virtual` 是否不存在** | `File(dataDir, "app_virtual").exists() == false` |
| 7 | `files/virtual` 的子目录 | `apks`/`apps`/`data`/`snapshots` 都存在 |
| 8 | **`is_path_hidden("/data/data/<host>/files")`** | 需要新的 native API `nativeTestHiddenPath(path)` |
| 9 | **签名 SHA256** | `getPackageInfo(...).signatures` 的摘要 |

### 2.4 CmdReceiver 加命令

```kotlin
const val ACTION_SELFCHECK = "com.nextvm.cmd.SELFCHECK"
ACTION_SELFCHECK -> {
    val i = Intent().apply {
        setClassName(context.packageName, "${context.packageName}.selfcheck.SelfCheckService")
    }
    try { context.startService(i); Log.i(TAG, "SELFCHECK dispatched to :selfcheck") }
    catch (t: Throwable) { Log.e(TAG, "SELFCHECK dispatch failed", t) }
}
```

---

## 测试更新（探针）

| 新增项 | 内容 | 判据 |
| --- | --- | --- |
| **`P31`** | `listFiles("/data/data")` 里有没有 `com.nextvm.app` | 任务 4 的 `readdir` 过滤是否生效 |
| **`P32`** | `getApplication()?.javaClass?.name` | 验证 guest Application 绑定 |
| **`P34`** | `readlink("/proc/self/fd/N")` 是否含 `com.nextvm.app`/`files/virtual` | 量化 native 层暴露 |

**`P31` 的实现**：
```java
// ProbeRunner 里
File[] list = new File("/data/data").listFiles();
boolean found = false;
if (list != null) for (File f : list) if ("com.nextvm.app".equals(f.getName())) { found = true; break; }
put("P31", "readdir", "host_visible", () -> found ? "host_visible" : "hidden");
```

**`P32`**：
```java
put("P32", "app", "getApplication", () -> {
    Application app = (Application) getApplication();
    return app == null ? "null" : app.getClass().getName();
});
```

**`P34`**：
```java
// 打开自己的一个文件，然后 readlink /proc/self/fd/N
```

---

## 影响范围

| 任务 | 文件 | 类型 |
| --- | --- | --- |
| **4** | `core/hook/src/main/cpp/native-hook.cpp` | 修改（`is_path_hidden` 重写 + `readdir`/`closedir` 新增） |
| **1** | `core/common/.../SettingsStore.kt` | **新增** |
| **1** | `core/common/build.gradle.kts` | 修改（加 hilt/timber，如需） |
| **1** | `app/.../CmdReceiver.kt` | 修改（+2 action + EntryPoint） |
| **1** | `feature/settings/.../SettingsViewModel.kt` | 修改（接 store） |
| **1** | `app/.../AppModule.kt` | 修改（+provide SettingsStore，如需） |
| **2** | `app/.../selfcheck/SelfCheckService.kt` | **新增** |
| **2** | `app/src/main/AndroidManifest.xml` | 修改（+service） |
| **2** | `app/.../CmdReceiver.kt` | 修改（+1 action） |
| **测试** | `probe/.../ProbeRunner.java` | 修改（+P31/P32/P34） |
| **测试** | `core/hook/.../NativeHookBridge.kt` | 修改（+`nativeTestHiddenPath`） |

**合计 11 个文件，5 个模块** → 触发"大量修改"条件，本文件落盘。

---

## 风险与回退

| 风险 | 缓解 |
| --- | --- |
| **`readdir` hook 影响性能** | 只在 `filter=true` 的 `DIR*` 上做路径拼接；其余直通 |
| **`readdir` hook 导致崩溃** | 先只 hook `readdir` 不 hook `readdir64`，验证后再加 |
| **`SettingsStore` 放 `core:common` 引入 Hilt 依赖** | 如果 `core:common` 没有 Hilt，改用纯类（不用 `@Inject`，手动传 `Context`） |
| **`:selfcheck` 进程增加内存** | 只在 `SELFCHECK` 命令时启动，`START_NOT_STICKY` 自动退出 |

**回退**：
```bash
git -C _ref/ShitJumpingVM revert <sha>
```

---

## 验证方法

### 任务 4
```powershell
# 1) is_path_hidden 前缀匹配
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg com.sandbox.probe
# 探针 P16 应返回 exists=false（原来 exists=true）
# 探针 P31 应返回 hidden（原来 host_visible）

# 2) readdir 过滤
adb logcat -d | Select-String "P31"
```

### 任务 1
```powershell
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.SETTINGS_GET
adb logcat -d -s CmdReceiver | Select-String "SETTINGS"
# 应输出 5 行，每个 key 带 default/real

adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.SETTINGS_SET --es key verboseLogging --es value true
adb logcat -d -s CmdReceiver | Select-String "SETTINGS_SET ok"
```

### 任务 2
```powershell
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.SELFCHECK
adb logcat -d -s SelfCheckService | Select-String "SELFCHECK done"
adb shell run-as com.nextvm.app cat files/probe-out/selfcheck.json
```
