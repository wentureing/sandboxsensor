# 诊断：`app_virtual/` vs `files/virtual/` —— 上游重构残留，不是有意设计

- 日期：2026-11-23
- 背景：`SandboxManager` 用 `context.getDir("virtual")`（→ `app_virtual/`），
  `VirtualEngine`/`PackageManagerProxy` 用 `File(context.filesDir, "virtual")`（→ `files/virtual/`）
- 问题：这是有意为之、恶性异常，还是别的原因？原生 Android 上这两个路径有功能差异吗？

---

## 一、先回答"原生 Android 上有没有功能差异"

### 1.1 两个 API 的真实语义

| API | 返回路径 | 说明 |
| --- | --- | --- |
| `context.getDir("virtual", MODE_PRIVATE)` | `/data/user/0/<pkg>/**app_virtual**` | **与 `files/` 平级**的私有目录，名字带 `app_` 前缀（Android 的命名约定） |
| `File(context.filesDir, "virtual")` | `/data/user/0/<pkg>/**files**/virtual` | `files/` 下的子目录 |
| `context.filesDir` | `/data/user/0/<pkg>/files` | 标准私有文件目录 |

### 1.2 功能差异：**几乎没有**

| 维度 | `app_virtual` | `files/virtual` |
| --- | --- | --- |
| 私有性 | ✅ 应用私有（`MODE_PRIVATE`） | ✅ 应用私有 |
| Auto Backup | 包含 | 包含 |
| 卸载时删除 | 是 | 是 |
| 设置里"清除数据" | 清 | 清 |
| 权限 / SELinux 标签 | 相同（都在 `/data/user/0/<pkg>/` 下） | 相同 |
| **是否被 `getFilesDir()` 扫到** | **否** | 是 |

**唯一的实际区别**是位置和命名前缀——**没有任何功能性差异**。

**所以：上游用两个路径，不是"原生功能需要"。**

---

## 二、那是"恶意异常"吗？也不是

### 2.1 设备实测

```
$ run-as com.nextvm.app ls -1 app_virtual/data/<每个实例>
com.sandbox.probe_1795353641715   -> sdcard
com.sandbox.probe_1795353657138   -> sdcard
mark.via_1795353711357            -> sdcard            ← 三个实例全都只有 sdcard

$ run-as com.nextvm.app ls -1 files/virtual/data/com.sandbox.probe_1795353657138
app_data cache code_cache databases external_cache external_files
files lib no_backup obb sdcard shared_prefs            ← 12 项，真实
```

### 2.2 代码里是怎么写的

```kotlin
// SandboxManager.createAppDataDir（设计上会建 11 个 subdir + sdcard）
fun createAppDataDir(instanceId: String, packageName: String) {
    val appDir = getAppDir(instanceId)              // app_virtual/data/<instance>
    for (subDir in DATA_SUBDIRS) {                  // files/cache/shared_prefs/databases/lib/...
        File(appDir, subDir).mkdirs()               // 11 个
    }
    val sdcard = getExternalStorageDir(instanceId)  // ensureDir(File(appDir, "sdcard"))
    ...
}
```

**如果它执行成功，`app_virtual/data/<instance>/` 应该有 12 项。实测只有 1 项（sdcard）。**

→ 说明 `DATA_SUBDIRS` 的 `mkdirs()` **实际没有产生目录**，
而 `sdcard` 是**另一个入口**建的（`getExternalStorageDir`，被 `VirtualContext` / `VirtualEnvironmentHook` 调用）。

### 2.3 但功能没坏

因为 guest 实际用的 `dataDir` 由**另一个地方**决定：

```kotlin
// PackageManagerProxy.kt:717（启动时构造 ApplicationInfo）
dataDir = "${context.filesDir}/${VIRTUAL_DIR}/${VIRTUAL_DATA_DIR}/${app.instanceId}"
        // = files/virtual/data/<instance>
```

而 `VirtualEngine.kt:468` 虽然也设过 `dataDir = sandboxManager.getAppDir(...)`（`app_virtual/...`），
**但它在安装流程里，启动时被 `PackageManagerProxy` 覆盖。**

→ **后者赢**，所以 guest 的数据落在 `files/virtual/data/<instance>`，
其中 `files`/`cache`/`databases` 等是 **guest 自己的 `LoadedApk` 建的**（Android 框架行为），
`lib` 是 `NativeLibManager.extractNativeLibs(allApkPaths, instanceId, File(virtualRoot, "data"))` 建的。

---

## 三、结论：这是**重构残留**，不是设计，也不是故障

| 判断 | 依据 |
| --- | --- |
| **不是有意为之** | `createAppDataDir` 的注释写着 "Create the **full** data directory tree"，明显是设计成主入口；但它实际产出不完整，且被绕过 |
| **不是恶性异常** | guest 安装/启动/读写全部正常，探针能写报告，Via 能启动 |
| **是"双轨并存 + 后写覆盖"** | `SandboxManager`（`app_virtual`）是早期实现；`VirtualEngine`/`PackageManagerProxy`（`files/virtual`）是后来接管；旧的那套没删干净 |
| **是否"兼容老旧/新式程序需要"** | **完全无关**。这两个路径都是**容器自己的内部实现**，guest 根本看不到——guest 看到的是 `dataDir`（由 `PackageManagerProxy` 决定） |

### 3.1 一个仍未解释的细节（不影响结论）

`createAppDataDir` 确实被调用（`VirtualEngine.kt:407`），但 `DATA_SUBDIRS` 的 11 个目录没建出来。
可能的原因：
- `mkdirs()` 失败但返回值未检查（静默失败）
- 设备上运行的容器版本（p17/p18）与 `_ref` 最新代码在这一点上有差异

**这不影响本设计的结论**，因为 `COLLECT_OUT` 只要遍历**真实数据所在的 `files/virtual/data/`** 即可。

---

## 四、对本设计的处置

| 项 | 决策 |
| --- | --- |
| `COLLECT_OUT` 遍历路径 | **`files/virtual/data/<instance>/files/`**（真实数据所在） |
| 是否修 `provideSandboxManager` | **暂不修**。它属于上游技术债，改动会影响 `SandboxManager` 的 25 个方法；且当前功能正常，修它的收益只在"目录整洁" |
| 是否清理 `app_virtual/` | 建议**在确认无引用后删除**（单独任务），但**不要和本设计混在一起** |
| 是否需要"兼容老旧/新式程序"的特殊处理 | **不需要**。这个差异不出现在 guest 可见的任何 API 上 |

### 4.1 如果将来要修，正确的方式

```kotlin
// AppModule.kt:228
- val virtualRoot = context.getDir("virtual", Context.MODE_PRIVATE)      // app_virtual
+ val virtualRoot = File(context.filesDir, VirtualConstants.VIRTUAL_DIR) // files/virtual
```

**修之前必须**：
1. 确认 `app_virtual/` 里的 `sdcard` 没有被任何在用代码依赖（目前看 `VirtualContext.getExternalFilesDir` 走的是 `getExternalAppFilesDir` → `getExternalStorageDir` → `app_virtual/.../sdcard`，**有依赖！**）
2. 做数据迁移（把 `app_virtual/data/<instance>/sdcard` 移到 `files/virtual/data/<instance>/sdcard`）

**第 1 点很关键**：`VirtualContext.getExternalFilesDir()` 现在返回的是 `app_virtual/.../sdcard/Android/data/<pkg>/files`，
如果直接改根，这些路径会变，**guest 的"外部存储"内容会全部"消失"**（实际是换了个目录）。

**所以这不是"1 行修复"，而是"带迁移的重构"** —— 更不该顺手做。
