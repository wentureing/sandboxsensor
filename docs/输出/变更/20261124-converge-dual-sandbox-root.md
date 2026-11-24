# 修改计划：收敛双轨沙箱（app_virtual → files/virtual）

- 日期：2026-11-24
- 任务：修复 1（用户明确要求执行）
- 目标：**让容器 UI 和 `CmdReceiver` 看到真实的沙箱数据**（`files/virtual`，32 文件 / 72 目录），
  而不是空壳（`app_virtual`，0 文件 / 13 目录）

---

## 一、完整代码路径调查（不是基于片段）

### 1.1 `SandboxManager` 的所有方法都基于同一个根

```
getAppDir(instanceId)  = ensureDir(File(dataRoot, instanceId))       // L143
dataRoot               = File(virtualRoot, VIRTUAL_DATA_DIR)          // L79
```

**其余 20+ 个方法都是 `ensureDir(File(getAppDir(instanceId), "<sub>"))` 的形式**：

| 方法 | 子目录 | 基于 |
| --- | --- | --- |
| `getFilesDir` | `files` | getAppDir |
| `getCacheDir` | `cache` | getAppDir |
| `getDatabasesDir` | `databases` | getAppDir |
| `getLibDir` | `lib` | getAppDir |
| `getCodeCacheDir` | `code_cache` | getAppDir |
| `getExternalFilesDir` | `external_files` | getAppDir |
| `getExternalStorageDir` | `sdcard` | getAppDir |
| `getExternalStoragePublicDir` | `<type>` | getExternalStorageDir |
| `createAppDataDir` | `DATA_SUBDIRS` | getAppDir |
| `getApkPath` | — | **`virtualRoot/apks/`** |
| `getTotalStorageSize` | — | **`virtualRoot`** |
| `collectProbeOutput` / `listProbeOutput` / `readProbeOutput` | — | **`context.filesDir`（真实根，已 workaround）** |

**结论**：**改 `virtualRoot` 一处，全部方法跟着改** ✓

### 1.2 `getDir("virtual")` 共有 **3 处**（不是 1 处）

| # | 位置 | 用途 | 是否必须改 |
| --- | --- | --- | --- |
| **1** | **`AppModule.kt:228`** `provideSandboxManager` | `CmdReceiver`（`collectProbeOutput`/`listProbeOutput`）+ `VirtualEnvironmentHook` | ✅ **必须** |
| **2** | **`FileManagerViewModel.kt:118`** | **UI 文件管理器的浏览起点**（`navigateTo(virtualRoot.absolutePath)`） | ✅ **必须** |
| 3 | `NativeHookBridge.kt:221` | `nativeSetVirtualDataRoot(virtualRoot)` | ⚠️ 可选（见 1.3） |

### 1.3 `NativeHookBridge:221` 是**死变量**，无实际影响

```cpp
// native-hook.cpp
L173: static std::string g_virtual_data_root;      // 定义
L6234: g_virtual_data_root.clear();                // 清空
L6265: g_virtual_data_root = std::string(r);       // 设置
L6267: LOGI("Virtual data root: %s", ...);         // 打日志
```

**全文件搜索 `g_virtual_data_root` 只有这 4 处，没有任何地方读它。**

而 `redirect_path`（L252）用的是 **`g_path_redirects`（显式规则表）**，不用它。

**所以**：`getDir("virtual")` 在这里**只影响一行日志**，改了更一致，不改也不出错。

### 1.4 数据对比（改后是否丢数据）

| 根 | 文件数 | 目录数 |
| --- | --- | --- |
| **`app_virtual/data`** | **0** | 13 |
| **`files/virtual/data`** | **32** | 72 |

**`app_virtual` 里一个文件都没有** → **改后零数据丢失** ✓

---

## 二、影响范围

| 文件 | 改动 | 类型 |
| --- | --- | --- |
| `app/src/main/java/com/nextvm/app/AppModule.kt` | L228 一行 | 修改 |
| `feature/filemanager/src/main/java/com/nextvm/feature/filemanager/FileManagerViewModel.kt` | L118 一行 | 修改 |
| `core/hook/src/main/java/com/nextvm/core/hook/NativeHookBridge.kt` | L221 一行 | 修改（可选，一致性） |

**3 个文件 / 3 行** → 触发条件"≥3 个文件" → **属于"大量修改"**，所以本文落盘。

---

## 三、逐文件 diff 预览

### 3.1 `AppModule.kt`

```diff
     fun provideSandboxManager(
         @ApplicationContext context: Context
     ): SandboxManager {
-        val virtualRoot = context.getDir("virtual", Context.MODE_PRIVATE)
+        // 与 VirtualEngine.kt:163 保持一致：真实沙箱根是 <dataDir>/files/virtual
+        // getDir("virtual") 返回的是 <dataDir>/app_virtual（空壳，只有空目录）
+        // 详见 docs/records/记录-双轨沙箱实证-UI看到空的真因.md
+        val virtualRoot = File(context.filesDir, "virtual")
         return SandboxManager(context, virtualRoot)
     }
```

### 3.2 `FileManagerViewModel.kt`

```diff
-    private val virtualRoot: File = context.getDir("virtual", Context.MODE_PRIVATE)
+    // 同 AppModule：真实沙箱根在 files/virtual，不是 app_virtual
+    private val virtualRoot: File = File(context.filesDir, "virtual")
```

**注意**：该类还有 4 处 `startsWith(virtualRoot.absolutePath)` 的边界检查（L173/L206/L208/L417/L455/L461）
—— 它们都基于这个字段，**改后自动正确** ✓

### 3.3 `NativeHookBridge.kt`（可选）

```diff
-                        val virtualRoot = ctx.getDir("virtual", android.content.Context.MODE_PRIVATE).absolutePath
+                        val virtualRoot = java.io.File(ctx.filesDir, "virtual").absolutePath
                         nativeSetVirtualDataRoot(virtualRoot)
```

---

## 四、风险

| # | 风险 | 评估 |
| --- | --- | --- |
| 1 | **`app_virtual` 里有数据丢失** | ❌ **不存在**（实测 0 个文件） |
| 2 | `FileManagerViewModel` 的边界检查失效 | ❌ 无影响（都基于同一字段） |
| 3 | `VirtualEnvironmentHook` 依赖旧根 | ⚠️ 需验证（它注入 `sandboxManager` 但**代码里没用 `sandboxManager.`**，见调查） |
| 4 | `collectProbeOutput` 受影响 | ❌ 无影响（它用 `context.filesDir`，与新根同源） |
| 5 | 旧 `app_virtual` 目录残留 | ⚠️ 不影响功能，但占空间（49 KB，可忽略） |

---

## 五、回退方案

```powershell
git -C _ref/ShitJumpingVM revert <sha>
# 或者手动改回：
#   AppModule.kt:228   -> context.getDir("virtual", Context.MODE_PRIVATE)
#   FileManagerViewModel.kt:118 -> context.getDir("virtual", Context.MODE_PRIVATE)
#   NativeHookBridge.kt:221 -> ctx.getDir("virtual", ...).absolutePath
```

---

## 六、验证方法

| # | 命令 | 判据 |
| --- | --- | --- |
| **1** | 装新容器 → 打开文件管理器 | **能看到 `data/`、`apks/`、`apps/`、`snapshots/`**（之前只有空的 `data/`） |
| **2** | 进 `data/<instance>/sdcard` | **能看到 13 个标准目录**（之前 0 个） |
| **3** | `adb shell run-as com.nextvm.app ls files/virtual/data` | 仍是 2 个实例（无变化） |
| **4** | `adb shell am broadcast ... COLLECT_OUT` | 仍能收集到探针文件（`collectProbeOutput` 用真实根，不受影响） |
| 5 | 打开某个 guest 的 files | 能看到真实文件（之前空） |

---

## 七、执行顺序

1. 改 3 个文件
2. 提交 + push（触发 CI）
3. **不阻塞等 CI**（按 AGENTS.md）
4. 装新包验证（等 CI 完成后）
