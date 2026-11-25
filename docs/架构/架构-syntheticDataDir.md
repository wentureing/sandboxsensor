# 架构：syntheticDataDir 与 pathFidelity（合成路径 / 路径保真）

> **这是 `VirtualConstants.syntheticDataDir` 及其同族函数的唯一说明文档。**
> 改这几个函数、或调整 `PATH_FIDELITY_WHITELIST` 之前，先读这里。

- 代码位置：`core/model/src/main/java/com/nextvm/core/model/ProcessSlot.kt`（`VirtualConstants`）
- 消费方：`core/sandbox/.../VirtualContext.kt`（13+ 处 `pathFidelity` 分支）、
  `core/hook/.../NativeHookBridge.kt`（`setupPathFidelityRedirections`）
- 日期：2026-11-25
- **核实基准**：容器 `baseline-v150 @ 6b5cfbe`（§3.2 / §4.3 已就地更正）
- 状态：⚠️ **部分过时** —— `synthetic*` 模板已迁到 `PathKinds`（数据表），本文件保留"用途/边界/缺陷"
- **多实例开发暂停（2026-11-25 用户决定）**：不要给合成路径加 slot 参数

---

## 一、用途：为什么不直接用沙箱路径

默认情况下 `VirtualContext` 返回的是**真实沙箱路径**：

```
/data/user/0/com.nextvm.app/files/virtual/data/<instanceId>/files
```

这条路径**暴露了三件事**：宿主包名、`virtual/data` 结构、实例 ID。任何读取
`context.getFilesDir()` 的应用都能一眼看出自己跑在双开容器里。

**`syntheticDataDir` 的作用**：给 guest 一个**看起来像真机安装**的路径，
而真实 I/O 由 hook 规则映射回沙箱。

```
guest 看到：  /data/user/0/<guestPkg>/files          ← 真机形态，实际不存在
真实位置：    <sandbox>/files                         ← hook 规则映射过去
```

**关键字：假路径 + 真映射。** 路径只用于"看起来对"，从不真正落盘。

---

## 二、六个合成路径函数（当前全部定义）

| 函数 | 返回值 | 用途 |
| --- | --- | --- |
| **`syntheticDataDir(pkg)`** | `/data/user/0/<pkg>` | data 目录根（`getDataDir`/`getFilesDir`/`getCacheDir`/`getDatabasePath`…） |
| `syntheticApkPath(pkg)` | `/data/app/~~VmK8x2==/<pkg>-VmK8x2==/base.apk` | APK 路径，**必须与 `/proc/self/maps` 显示的一致** |
| `syntheticLibDir(pkg)` | `/data/app/~~VmK8x2==/<pkg>-VmK8x2==/lib/arm64` | native lib 目录 |
| **`syntheticExternalDir(pkg)`** | `/storage/emulated/0/Android/data/<pkg>` | external data 根（`2026-11-25 新增`） |
| **`syntheticExternalCacheDir(pkg)`** | `/storage/emulated/0/Android/data/<pkg>/cache` | external cache（同日新增） |
| **`syntheticObbDir(pkg)`** | `/storage/emulated/0/Android/obb/<pkg>` | OBB 目录（同日新增） |

**`SYNTHETIC_INSTALL_TOKEN = "VmK8x2"`** —— 这个 token 不是随意取的：
native 侧 `synthetic_apk_path()` 用的是同一个值，**Java 层路径与 `/proc/self/maps`
必须一致**，否则应用做自我校验时会发现矛盾。

---

## 三、可用的层级：哪些 API 会返回合成路径

### 3.1 已覆盖（`pathFidelity == true` 时返回合成路径）

| API | 返回 |
| --- | --- |
| `getDataDir()` | `/data/user/0/<pkg>` |
| `getFilesDir()` | `/data/user/0/<pkg>/files` |
| `getCacheDir()` | `/data/user/0/<pkg>/cache` |
| `getCodeCacheDir()` | `/data/user/0/<pkg>/code_cache` |
| `getNoBackupFilesDir()` | `/data/user/0/<pkg>/no_backup` |
| `getDatabasePath(name)` | `/data/user/0/<pkg>/databases/<name>` |
| `getDir(name, mode)` | `/data/user/0/<pkg>/app_<name>` |
| **`getExternalFilesDir(type)`** | `/storage/emulated/0/Android/data/<pkg>/files[/type]` |
| **`getExternalCacheDir()`** | `/storage/emulated/0/Android/data/<pkg>/cache` |
| **`getObbDir()`** | `/storage/emulated/0/Android/obb/<pkg>` |
| `getApplicationInfo().sourceDir`（经规则） | `/data/app/~~VmK8x2==/<pkg>-VmK8x2==/base.apk` |
| native lib dir（经规则） | `/data/app/~~VmK8x2==/.../lib/arm64` |

### 3.2 未覆盖（仍返回沙箱路径 → **仍暴露**）

| API | 现状 | 说明 |
| --- | --- | --- |
| **`getExternalMediaDirs()`** | **已覆盖**（`54f3649` 补）：`pathFidelity` 时返回 `/storage/emulated/0/Android/media/<pkg>`；`contentPathsUseReal=false` 时返回真沙箱路径 | 2026-11-25 更正：旧文写"未加分支"，代码已补（`VirtualContext.kt:217`） |
| `getExternalFilesDirs()` / `getExternalCacheDirs()` / `getObbDirs()` | 分别调用上面三个单数方法 | **自动跟随**，不需要单独改 |

### 3.3 不需要覆盖（**重要，否则会做错**）

| API | 为什么不用改 |
| --- | --- |
| **`Environment.getExternalStorageDirectory()`** | 返回**共享存储根** `/storage/emulated/0` —— 真机就是这个值，**不含包名，不暴露**。`Environment.java` 里全部路径来自进程级静态 `sCurrentUser.getExternalDirs()[0]`，改它等于改整个进程的共享存储视图，副作用远大于收益 |
| **`Environment.getExternalStoragePublicDirectory(type)`** | 同上，`/storage/emulated/0/<type>` |
| **`MediaStore.*`** | 走 `ContentResolver` + `content://` URI，**不返回文件绝对路径**；`MediaColumns.DATA`（`_data` 列）在 Android 10+ scoped storage 下第三方 app 已不可用 |
| `getSharedPreferences()` | 走宿主目录 + 扁平化 key（`nextvm_<instanceId>_<name>.xml`），不经文件系统路径 |

**一句话**：需要合成的是**「每 app 私有的目录」**（含包名），
不是**「共享存储根」**（不含包名）。

---

## 四、现状：只对白名单生效

### 4.1 白名单（当前内容）

```kotlin
private val PATH_FIDELITY_WHITELIST: Set<String> = setOf(
    CM_PACKAGE,      // com.greenpoint.android.mc10086.activity —— 原始定向对抗目标
    "mark.via",      // 2026-11-25 加入：验证 file:// 与 WebView MIME
)
fun isPathFidelityGuest(packageName: String) = packageName in PATH_FIDELITY_WHITELIST
```

**加包名到集合即可启用。** 不在白名单的 guest 走沙箱真实路径。

### 4.2 为什么最初只对一个包

代码里没写原因。从形态反推：「让 `/data/user/0/<pkg>` 看起来存在」最匹配的需求是
**对抗反双开检测**（这类 app 会 `File.exists("/data/user/0/<ownPkg>")` 或比对
`getDataDir()` 与 `/proc` 信息）。所以最初是**定向对抗**，不是通用能力。

### 4.3 已知缺陷（写在代码 KDoc 里，此处再记一次）

| # | 缺陷 | 后果 | 解法 |
| --- | --- | --- | --- |
| **1** | `syntheticDataDir(pkg)` **不含 instanceId** | 同一 app 装两份 → 合成路径相同 → 规则表 `ConcurrentHashMap<from,to>` 后注册覆盖前者 → **先注册的实例所有 I/O 跑到另一个实例的沙箱（数据损坏级）** | 按槽位映射：slot 0 → `/data/user/0/<pkg>`，slot N → `/data/user/<10+N>/<pkg>`（Android 原生多用户就是 user 10+，形态合法） |
| 2 | `getExternalMediaDirs()` 未加分支 | media 目录仍暴露 | 补分支（低优先） |
| 3 | 路径经 binder 传出时 | `FileProvider` / `Intent` 里的 `file://` 由**系统侧**解析成真实路径 → 可能拿到宿主数据 | 需单独评估，所有 pathFidelity 方案的共同风险 |

**当前明确只支持单实例。**

> **2026-11-25：多实例开发已暂停（用户决定）。** 上表缺陷 1 因此保持现状，
> 不要在 B 阶段（`PathKinds` 数据表）顺手把 slot 塞进合成路径签名——那会牵动 24 条规则与
> `VirtualContext`/`PackageManagerProxy` 的全部调用点。恢复多实例时，先改这里再改代码。

---

## 五、性能代价：≈ 0

| 项 | 代价 |
| --- | --- |
| `isPathFidelityGuest()` | 一次 `Set.contains`（2 元素） |
| 每个 `get*Dir()` 的 if 分支 | 一次 boolean 判断 |
| 合成路径构造 | 一次 `File(String)`，**不 touch 文件系统** |
| 规则条数 | +3 条 → 最长前缀匹配多 3 次 `startsWith` |

**真正的开销不来自 pathFidelity**，而来自 `SandboxManager.ensureDir()`：

```kotlin
if (ensuredDirs.contains(path) && file.isDirectory) {   // ← 每次一次 stat
    return file
}
```

但这条**本来就存在**（非 pathFidelity 分支也调同一个方法）。若要优化，
目标是 `ensureDir` 的 `isDirectory`（短 TTL 缓存），不是 pathFidelity。

---

## 六、数据流（一条完整链路）

```
guest 调 context.getExternalFilesDir("offline")
  │
  ├─ [pathFidelity = true]
  │    sandboxManager.getExternalAppFilesDir(...)          建立沙箱目录
  │    return File("/storage/emulated/0/Android/data/<pkg>/files/offline")
  │           ↑ 假路径，实际不存在
  │
guest 拿它做 File.listFiles()
  │
  ├─ hook: hooked_ufs_list0
  │    ufs_get_path -> "/storage/emulated/0/Android/data/<pkg>/files/offline"
  │    translatePath:
  │       规则 /storage/emulated/0/Android/data/<pkg>/ -> <sandbox>/sdcard/Android/data/<pkg>/
  │       => "<sandbox>/sdcard/Android/data/<pkg>/files/offline"
  │    opendir(沙箱路径) -> 真实文件
  │
guest 看到 2 个 .mht，以为自己在 /storage/emulated/0/...
```

**规则来源**：`NativeHookBridge.setupAppRedirections`（`2026-11-25` 修正过目标——
原先指向 `external_data/`，那个目录从来没被写入过）。

---

## 七、相关文档

| 文档 | 内容 |
| --- | --- |
| `docs/设计/设计-讨论-pathFidelity白名单与路径不暴露的边界.md` | 白名单设计讨论、external 缺口、多实例方案对比 |
| `docs/设计/设计-为什么不开hook也能工作与hook是接管而非增强.md` | 为什么 `VirtualContext` 是主力、hook 是兜底 |
| `docs/架构/架构-为什么native在UnixFileSystem与跨版本兼容.md` | `File` 的操作落在哪个 native 方法上 |
| `docs/输出/记录/记录-对照AOSP源码指出的实现错误.md` | 每个 hook 与 AOSP 的行为差异 |
