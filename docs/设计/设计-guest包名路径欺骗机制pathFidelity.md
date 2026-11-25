# 设计：让 guest 包名路径「直接可用但实际不存在」—— pathFidelity 机制

- 日期：2026-11-25
- 触发：用户指出 —— gaia（应用隐藏大师）的路径是把 guest 包名拼在自己包名后面
  （`<hostPkg>/<guestPkg>/...`），这**暴露了沙箱**；要求改为「guest 包名路径直接可用，
  但实际不存在，由沙箱欺骗」
- 结论先行：**这个机制本项目已经有了**（`pathFidelity` / `v135-pathfidelity`），
  只是**只对一个白名单包启用**。要做的不是新建机制，而是**评估能否全量启用**。

---

## 一、gaia 的做法 vs 本项目的做法

| | 路径形态 | 是否暴露沙箱 |
| --- | --- | --- |
| **gaia（用户描述的）** | `/data/data/<hostPkg>/<guestPkg>/...` | ✅ **暴露**（一眼看出是双开） |
| **本项目 pathFidelity** | `/data/user/0/<guestPkg>/...` | ❌ **不暴露**（看起来就是正常安装） |
| **本项目默认（非 pathFidelity）** | `/data/user/0/<hostPkg>/files/virtual/data/<instance>/` | ✅ **暴露**（长路径 + 含 host 包名） |

**所以 pathFidelity 就是用户要的形态**：guest 拿到的路径与真机安装完全一致，
文件实际落在沙箱里，由 hook 做路径映射。

---

## 二、机制的三个组成部分（都已在代码里）

### 1. 合成路径生成（`core/model/.../ProcessSlot.kt` L152-168）

```kotlin
/** Real stock data dir for the guest package (deterministic). */
fun syntheticDataDir(packageName: String): String = "/data/user/0/$packageName"

/** Stock-install APK path shape (same token as the maps spoof). */
fun syntheticApkPath(packageName: String): String =
    "/data/app/~~$SYNTHETIC_INSTALL_TOKEN==/$packageName-$SYNTHETIC_INSTALL_TOKEN==/base.apk"

/** Stock-install native lib dir shape. */
fun syntheticLibDir(packageName: String): String =
    "/data/app/~~$SYNTHETIC_INSTALL_TOKEN==/$packageName-$SYNTHETIC_INSTALL_TOKEN==/lib/arm64"
```

**`SYNTHETIC_INSTALL_TOKEN = "VmK8x2"`** —— 与 `/proc/self/maps` 里显示的一致，
所以 Java 层路径和 native 层视图不会互相矛盾。

### 2. Context 返回合成路径（`core/sandbox/.../VirtualContext.kt`，13 处 `pathFidelity` 分支）

```kotlin
private val pathFidelity: Boolean = VirtualConstants.isPathFidelityGuest(guestPackageName)

override fun getFilesDir(): File =
    if (pathFidelity) {
        sandboxManager.getFilesDir(instanceId)                 // 先确保沙箱目录存在
        File(VirtualConstants.syntheticDataDir(guestPackageName), "files")   // 再返回假路径
    } else {
        sandboxManager.getFilesDir(instanceId)
    }
```

覆盖的方法：`getFilesDir` / `getCacheDir` / `getCodeCacheDir` / `getDatabasePath` /
`getDir` / `getNoBackupFilesDir` / `getDataDir`（L219 处还有 `detachedAppInfoEnabled` 并联）。

### 3. I/O 映射回沙箱（`core/hook/.../NativeHookBridge.kt` L592）

```kotlin
fun setupPathFidelityRedirections(guestPackageName, instanceId, sandboxDataDir, apkPath) {
    setupAppRedirections(...)                     // /data/user/0/<guest>/ -> <sandbox>/
    // 再加 APK / lib 的合成路径规则
}
```

**所以整条链是**：

```
guest 调 getFilesDir()
  -> VirtualContext 返回 /data/user/0/<guestPkg>/files        （假路径，实际不存在）
guest 用这个路径做 I/O
  -> hook 规则 /data/user/0/<guestPkg>/ -> <sandbox>/         （真映射）
  -> 文件真的落在 <sandbox>/files
```

**这完全满足用户要求：路径直接可用、实际不存在、由沙箱欺骗。**

---

## 三、现状：只对一个包启用

```kotlin
const val CM_PACKAGE = "com.greenpoint.android.mc10086.activity"
fun isPathFidelityGuest(packageName: String): Boolean = packageName == CM_PACKAGE
```

**via（mark.via）走的是默认分支** —— 返回的是
`/data/user/0/com.nextvm.app/files/virtual/data/<instance>/`，
**这就是为什么 guest 能看到宿主包名。**

---

## 四、为什么当初只开一个包（需要确认的风险）

代码里没有直接写理由，但有两处线索指向**副作用**：

### 线索 1：v147 的 MIME 问题（`VirtualContext.getDataDir()` 的注释）

```
// v147：**这是 MIME 问题的关键点**。
// WebView 判断一个 file:// 是否"落在自己的数据目录内"，用的是
// context.getDataDir()（不是 getApplicationInfo().dataDir —— v146 已证明改那个无效）。
// 实测证据：沙箱里存在 <instance>/app_webview_<pkg>/，说明 WebView 确实用
// VirtualContext 的目录方法拿数据目录；此前 getDataDir() 始终返回沙箱 appDir，
// 而待加载的 html 也在 appDir/files 下 -> 「文件在 dataDir 内」-> 走特殊分支 -> text/plain。
```

**含义**：一旦 `getDataDir()` 返回合成路径，WebView 对 `file://` 的 MIME 判定会走另一条分支。
**这会让 `file://` 页面（via 的核心场景）渲染错。** 这很可能是"只开一个包"的原因。

### 线索 2：`getDataDir()` 已经是 `pathFidelity || detachedAppInfoEnabled()`

说明 `getDataDir` 这条最敏感，做了额外并联。

---

## 五、全量启用前必须回答的四个问题

| # | 问题 | 怎么验 |
| --- | --- | --- |
| 1 | `pathFidelity` 全开会不会重现 v147 的 `text/plain` MIME 问题？ | 用 via 打开 `file://` 本地 html，看是否仍按 text/html 渲染 |
| 2 | 合成路径 `/data/user/0/<guestPkg>/` 与宿主真实路径是否冲突？ | 若宿主上真有同名包，`/data/user/0/<guestPkg>` 是真实目录 -> hook 会把它指向沙箱，**可能影响宿主自身**。必须确认规则只在 guest 进程注册（P0-3 已保证主进程跳过） |
| 3 | `getExternalFilesDir` 是否也要合成？ | 现在 external 路径**不**走 pathFidelity（只有 data 目录走）-> 外部存储仍暴露 `<hostPkg>/files/virtual/...`。要彻底不暴露，external 也得合成 |
| 4 | 多实例（同包双开）怎么办？ | 合成路径只含包名，不含 instanceId -> 两个实例会指向同一路径。需要按 instance 区分的方案 |

**第 4 点最关键**：`syntheticDataDir(packageName)` **不含 instanceId**，
所以双开同一应用时两个实例的假路径会撞车。若要全量启用，需要
`syntheticDataDir(packageName, instanceId)` 之类的改造（比如按 slot 加后缀，
但那又会暴露——需要权衡）。

---

## 六、建议（**不执行**）

| 优先 | 动作 |
| --- | --- |
| P1 | 先只对 **via** 打开 `pathFidelity`，验证 `file://` MIME 是否正常（回答五-1） |
| P2 | 若 MIME 正常，扩展到 external 目录（五-3） |
| P3 | 设计多实例方案（五-4） |
| P4 | 全量启用 |

**当前不动手**：这一项会改变所有 guest 的路径形态，风险远大于修一个规则，必须先在
via 上做单点验证。
