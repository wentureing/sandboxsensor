# 双轨沙箱实证：为什么容器 UI 里看到的沙箱是空的

- 日期：2026-11-24
- 起因：用户发现 —— 在容器 home 点开 files，看到 `data/sandbox/sdcard` 里面什么都没有，
  且"文件明显被隐藏部分"，认为应该尽可能多展示
- 结论：**不是"被隐藏"，是 UI 读错了根** —— 真实数据在 `files/virtual`，UI 读的是 `app_virtual`（空壳）

---

## 一、实测数据（vivo A15，2026-11-24）

```
# 真实数据的根
$ run-as com.nextvm.app ls -1 files/virtual/data
com.sandbox.probe_1795445133994 | mark.via_1795488613994          ← 2 个实例

$ run-as com.nextvm.app ls files/virtual/data/com.sandbox.probe_1795445133994/sdcard | wc -l
13                                                                 ← 13 个目录

$ run-as com.nextvm.app du -sk files/virtual
4077  KB                                                           ← 4 MB

# UI 读的根（SandboxManager.virtualRoot）
$ run-as com.nextvm.app ls -1 app_virtual/data
com.sandbox.probe_1795442718610 | com.sandbox.probe_1795443003679 |
com.sandbox.probe_1795444295165 | com.sandbox.probe_1795444460820 |
com.sandbox.probe_1795445133994 | mark.via_1795488613994          ← 6 个实例（4 个是墓碑）

$ run-as com.nextvm.app ls app_virtual/data/com.sandbox.probe_1795445133994/sdcard | wc -l
0                                                                  ← 空！

$ run-as com.nextvm.app du -sk app_virtual
49  KB                                                             ← 49 KB
```

### 对比表

| 根 | 路径 | 实例数 | `sdcard` 条目 | 大小 |
| --- | --- | --- | --- | --- |
| **真实** | `files/virtual/data/` | 2 | **13** | **4077 KB** |
| **UI 读的** | `app_virtual/data/` | **6**（含 4 个墓碑） | **0** | **49 KB** |

---

## 二、根因：`SandboxManager.virtualRoot` 指向了错误的位置

```kotlin
// AppModule.kt:228
val virtualRoot = context.getDir("virtual", Context.MODE_PRIVATE)   // → <dataDir>/app_virtual
return SandboxManager(context, virtualRoot)
```

**`Context.getDir("virtual")` 返回的是 `<parent>/app_virtual`，与 `files/` 平级**，
而**真实数据在 `<parent>/files/virtual`**。

```kotlin
// VirtualEngine.kt 用的是
File(context.filesDir, "virtual")     // → <dataDir>/files/virtual  ✅ 真实
```

**所以两套代码用两个不同的根**：
- **`VirtualEngine` / `PackageManagerProxy` / guest 的数据** → `files/virtual`（**真实**）
- **`SandboxManager`（UI 通过它列目录）** → `app_virtual`（**空壳**）

---

## 三、这解释了三件事

### 3.1 用户看到"沙箱是空的"

**UI 通过 `SandboxManager` 列目录** → 读 `app_virtual/data/<instance>/sdcard` → **0 条目**。

**不是"被隐藏"，是"读错了地方"。**

### 3.2 "文件明显被隐藏部分"

`app_virtual` 上有 **6 个实例**，但 `files/virtual` 只有 2 个 ——
**多出的 4 个是已卸载实例的墓碑**（`app_virtual` 里残留的目录）。

所以用户看到的列表可能既**少了真实内容**，又**多了幽灵实例**。

### 3.3 我之前的判断错了：L1 目录骨架**早就做了**

```
$ ls files/virtual/data/<inst>/sdcard
Alarms  Android  Audiobooks  DCIM  Documents  Download
Movies  Music  Notifications  Pictures  Podcasts  Recordings  Ringtones
```

**13 个目录（AOSP 12 个 + Android 13 新增的 `Audiobooks`）已经预置好了。**

**所以"沙箱 `/sdcard` 是空的"这个判断完全错误** —— 真实沙箱里有完整的目录骨架，
只是 UI 看不到。

---

## 四、这意味着 P2-1（收敛双轨沙箱）的优先级要提升

### 4.1 之前 P2-1 被定为"2 天，高风险，需迁移"

理由是"要迁移 `app_virtual/data/<id>/sdcard/**`"。

**但现在看**：
- **`app_virtual` 只有 49 KB，且 `sdcard` 全是空目录** → **几乎没有数据要迁移**
- **真实数据（4077 KB）已经全在 `files/virtual`**
- **所以迁移量接近于零**

### 4.2 修正后的评估

| 项 | 之前 | **现在** |
| --- | --- | --- |
| 迁移数据量 | "需迁移 `app_virtual/data/<id>/sdcard/**`" | **49 KB，且都是空目录** |
| 风险 | 高 | **低** |
| 工作量 | 2 天 | **0.5 天**（改一行 + 清理墓碑） |

**改动**：
```kotlin
// AppModule.kt:228
val virtualRoot = File(context.filesDir, "virtual")    // 与 VirtualEngine 一致
return SandboxManager(context, virtualRoot)
```

**收益**：
- **容器 UI 能看到真实沙箱内容**（用户诉求"尽可能多展示"）
- **消除幽灵实例**（`app_virtual` 上的 4 个墓碑）
- **`SandboxManager` 的 25 个公共方法全部指向真实数据**（`VirtualContext` 调的那 8 个也受益）

### 4.3 但要注意

`SandboxManager` 有 **25 个公共方法**，其中 `VirtualContext` 调 8 个
（`getAppDir`/`getFilesDir`/`getCacheDir`/`getCodeCacheDir`/`getDatabasesDir`/
`getExternalAppFilesDir`/`getLibDir`/`getApkPath`）。

**如果这些方法现在指向 `app_virtual`，而 guest 实际使用 `files/virtual`** ——
**那 guest 的这些目录是从哪来的？**

**可能性**：
1. `VirtualContext` 实际用的不是 `SandboxManager`（而是 `VirtualEngine` 自己算的路径）
2. 或者 `SandboxManager` 的 `app_virtual` 里也创建了目录（但实测 `sdcard` 是 0 条目，说明没创建）

**必须先查清**：`VirtualContext` 到底调谁。**这是改这一行之前必须验证的。**

---

## 四点五、决定性证据：代码里已经有 workaround，作者知道这个问题

### 4.5.1 `SandboxManager` 没有 fallback

```kotlin
// SandboxManager.kt:79
private val dataRoot = File(virtualRoot, VirtualConstants.VIRTUAL_DATA_DIR)

// L143
fun getAppDir(instanceId: String): File {
    return ensureDir(File(dataRoot, instanceId))     // ensureDir 会 mkdirs
}

// L150
fun getFilesDir(instanceId: String): File {
    return ensureDir(File(getAppDir(instanceId), "files"))
}
```

**没有"两个根都检查"的逻辑** —— 直接用 `virtualRoot`（`app_virtual`）。

### 4.5.2 但 `collectProbeOutput` 明确绕过了它

```kotlin
// SandboxManager.kt:398
get() = File(context.filesDir, PROBE_OUT_DIR)

// L403 —— 原文注释
// ⚠️ 用真实沙箱根 `files/virtual/data`，**不用** `virtualRoot`：

// L410
context.filesDir,
```

**作者在这段代码里明确写了"不用 `virtualRoot`"** ——
说明**双轨问题是已知的**，并且在"探针输出"这一个场景做了 workaround。

**但 UI（通过 `SandboxManager` 的其它 25 个方法）没有绕过。**

### 4.5.3 所以这是"已知问题的未完成修复"

| 场景 | 是否绕过 | 结果 |
| --- | --- | --- |
| `collectProbeOutput` / `listProbeOutput` | ✅ 用 `context.filesDir` | **正常**（P0-2 实测过） |
| **UI 列沙箱目录** | ❌ 用 `virtualRoot` | **看到空** |
| `VirtualContext.getFilesDir()` | ❌ 用 `virtualRoot` | ⚠️ **但实测探针 `J01` 返回的是真实路径** |

**最后一行有矛盾，需要单独查**：
- `VirtualContext` 调 `sandboxManager.getFilesDir(instanceId)` → `app_virtual/...`
- **但探针 `J01`（`getDataDir()`）返回 `files/virtual/data/<instance>`**

**可能原因**：
1. `getDataDir()` 不是走 `getFilesDir()`（`Context.getDataDir()` 是另一个方法）
2. 或 `VirtualContext` 在别处被替换了路径

**这个矛盾在改 `AppModule.kt:228` 之前必须查清** —— 否则可能把现在能工作的路径改坏。

## 五、结论与行动

| # | 结论 | 行动 |
| --- | --- | --- |
| 1 | **UI 看到空 = 读错根**，不是隐藏 | 改 `AppModule.kt:228` 指向 `files/virtual` |
| 2 | **L1 目录骨架早就做了**（13 个目录） | 撤销"预置目录"这个待办 |
| 2.5 | **代码里已有 workaround**（`collectProbeOutput` 注释写明"不用 virtualRoot"） | 说明这是**已知未完成修复**，不是新发现 |
| 3 | **`app_virtual` 只有 49 KB 空目录** | 迁移量近零，P2-1 风险从"高"降到"低" |
| 4 | **改动前必须先查**：`VirtualContext` 调 `SandboxManager`（指向 `app_virtual`），但探针 `J01` 返回的是 `files/virtual` —— **这个矛盾不清，不能改** | **前置调查（已确认矛盾存在）** |
| 5 | 用户诉求"尽可能多展示" | 修根后自然满足（真实数据 4077 KB） |

---

## 六、用户其他两个决策（记录）

### 6.1 L2 示例文件：**不做**

> 使用过程中问题会解决（按需导入即可）。

### 6.2 L3 共享真实 `/sdcard`：**明确不做**

> 防止异常和过多功能点，**L3 与 L4 重复**。

**用户判断正确**：L3（共享真实）和 L4（按需导入）本质都是"让 guest 看到沙箱外的内容"，
只是 L3 是全量、L4 是按需。**保留 L4 即可**。
