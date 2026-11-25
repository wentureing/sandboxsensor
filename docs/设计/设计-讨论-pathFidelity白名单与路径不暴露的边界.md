# 设计讨论：pathFidelity 白名单与「路径不暴露」的边界

- 日期：2026-11-25
- 触发：用户要求直接讨论「`pathFidelity` 只对 `CM_PACKAGE` 启用」这个设计
- 本文包含**一处对前一份文档的更正**（见第二节）

---

## 一、两个关键事实（代码原文）

### 事实 1：`getDataDir()` 的两条分支

```kotlin
override fun getDataDir(): File {
    // v147：**这是 MIME 问题的关键点**。
    //
    // WebView 判断一个 file:// 是否"落在自己的数据目录内"，用的是
    // context.getDataDir()（不是 getApplicationInfo().dataDir —— v146 已证明改那个无效）。
    // 实测证据：沙箱里存在 <instance>/app_webview_<pkg>/，说明 WebView 确实用
    // VirtualContext 的目录方法拿数据目录；此前 getDataDir() 始终返回沙箱 appDir，
    // 而待加载的 html 也在 appDir/files 下 -> 「文件在 dataDir 内」-> 走特殊分支 -> text/plain。
    val dir = if (pathFidelity || detachedAppInfoEnabled()) {
        File(VirtualConstants.syntheticDataDir(guestPackageName))   // /data/user/0/<pkg>
    } else {
        appDir                                                       // <sandbox>/（沙箱）
    }
    ...
}
```

### 事实 2：`setupPathFidelityRedirections` 只补了 APK 与 lib

```kotlin
fun setupPathFidelityRedirections(guestPackageName, instanceId, sandboxDataDir, apkPath) {
    setupAppRedirections(guestPackageName, instanceId, sandboxDataDir)   // data/external/obb/host_ext
    addPathRedirection(VirtualConstants.syntheticApkPath(guestPackageName), apkPath)
    addPathRedirection(VirtualConstants.syntheticLibDir(guestPackageName) + "/", "$sandboxDataDir/lib/")
}
```

---

## 二、更正：v147 的 MIME 问题**不是** pathFidelity 引发的

**我上一份文档（`设计-guest包名路径欺骗机制pathFidelity.md`）第四节把它列为「全量启用的风险」，这是错的。**

### 为什么错

v147 那次实测里，**`pathFidelity` 对 via 是关闭的** → `getDataDir()` 走的是 **`else` 分支 = 沙箱路径 `appDir`**。

```
v147 的情况：
  getDataDir()      = <sandbox>/                          沙箱
  待加载的 html     = <sandbox>/files/xxx.html            也在沙箱
  → WebView 判定「文件在 dataDir 内」→ 走特殊分支 → text/plain   ❌

启用 pathFidelity 后：
  getDataDir()      = /data/user/0/<guestPkg>             假路径（实际不存在）
  待加载的 html     = <sandbox>/files/xxx.html            仍在沙箱
  → html 不在 /data/user/0/<guestPkg> 之下
  → WebView 判定「文件不在 dataDir 内」→ 走常规分支            ✅ 很可能反而修好
```

**结论：`pathFidelity` 对 MIME 问题可能是解药而不是毒药 —— 但这只是推理，必须实测。**

**这个纠正很重要**，因为它把 `pathFidelity` 从「高风险」重新定性为「可能有益，但需验证」。

---

## 三、`CM_PACKAGE` 白名单的真实作用（推测 + 依据）

```kotlin
const val CM_PACKAGE = "com.greenpoint.android.mc10086.activity"   // 中国移动某 app
fun isPathFidelityGuest(packageName: String): Boolean = packageName == CM_PACKAGE
```

代码里没有注释说明原因。**从设计形态反推**：

`pathFidelity` 做的事是「让 `/data/user/0/<pkg>` 看起来存在」。**谁需要这个？**

| 需求 | 是否匹配 |
| --- | --- |
| 隐私（不暴露沙箱） | 部分匹配，但只对一个包做隐私没意义 |
| **对抗反双开检测** | ✅ **最匹配** —— 这类 app 会 `File.exists("/data/user/0/<ownPkg>")` 或比对 `getDataDir()` 与 `/proc` 信息，发现不一致就拒绝运行 |
| 修某个 app 的特定崩溃 | 也可能 |

**所以 `pathFidelity` 的原始动机大概率是「针对性强」的定向对抗**，而不是通用能力。这解释了为什么它是**白名单而不是全开** —— 不是不敢开，而是**开了没人验证过别的 app 会不会踩坑**。

---

## 四、当前真实的「暴露点」清单（按路径族）

用 `pathFidelity` 开启/关闭两种情况对比，看哪些路径会暴露沙箱：

| 路径族 | pathFidelity 关闭（现状 for via） | pathFidelity 开启 |
| --- | --- | --- |
| `getFilesDir()` | `<sandbox>/files` ❌**暴露** | `/data/user/0/<pkg>/files` ✅ |
| `getCacheDir()` | `<sandbox>/cache` ❌ | `/data/user/0/<pkg>/cache` ✅ |
| `getDatabasePath()` | `<sandbox>/databases/x` ❌ | `/data/user/0/<pkg>/databases/x` ✅ |
| `getDataDir()` | `<sandbox>/` ❌ | `/data/user/0/<pkg>` ✅ |
| **`getExternalFilesDir()`** | `<sandbox>/sdcard/Android/data/<pkg>/files` ❌ | **仍然 ❌（没有 pathFidelity 分支）** |
| **`getExternalCacheDir()`** | 同上 ❌ | **仍然 ❌** |
| **`getObbDir()`** | `<sandbox>/sdcard/Android/obb/<pkg>` ❌ | **仍然 ❌** |
| **`getExternalMediaDirs()`** | `<sandbox>/sdcard/Android/media/<pkg>` ❌ | **仍然 ❌** |
| `getApplicationInfo().sourceDir` | 合成 APK 路径 ✅（`syntheticApkPath`） | 同 |
| native lib dir | 合成 lib 路径 ✅ | 同 |

### 关键结论

> **即使全量开启 `pathFidelity`，external 存储仍然暴露沙箱。**
>
> 因为 `getExternalFilesDir` 等方法**根本没有 `pathFidelity` 分支** —— 它们直接返回
> `sandboxManager.getExternalAppFilesDir(...)`，也就是 `<appDir>/sdcard/Android/data/<pkg>/files`。

**所以「不暴露沙箱」这件事，`pathFidelity` 只做了一半（data 目录），external 完全没做。**

这是设计上最重要的一条 —— 用户的目标不是"打开 pathFidelity"，而是**"没有任何路径暴露沙箱"**，那需要 external 也合成。

---

## 五、external 合成的设计选项

要让 `getExternalFilesDir()` 也返回 `/storage/emulated/0/Android/data/<pkg>/files`（假路径），需要三件事：

| # | 需要什么 | 现状 |
| --- | --- | --- |
| 1 | `syntheticExternalDir(pkg)` 常量 | **不存在**，需要新增 |
| 2 | `VirtualContext.getExternalFilesDir` 加 `pathFidelity` 分支 | 没有 |
| 3 | 规则 `/storage/emulated/0/Android/data/<pkg>/` → `<sandbox>/sdcard/Android/data/<pkg>/` | **已有**（本轮刚修）✓ |

**第 3 项已经有了**，所以 external 合成**只差前两项**，改动很小：

```kotlin
// VirtualConstants
fun syntheticExternalDir(packageName: String): String =
    "/storage/emulated/0/Android/data/$packageName"

// VirtualContext
override fun getExternalFilesDir(type: String?): File {
    val dir = if (pathFidelity) {
        sandboxManager.getExternalAppFilesDir(instanceId, guestPackageName, type)  // 确保存在
        File(VirtualConstants.syntheticExternalDir(guestPackageName), "files" + (type?.let { "/$it" } ?: ""))
    } else {
        sandboxManager.getExternalAppFilesDir(instanceId, guestPackageName, type)
    }
    ...
}
```

**但要小心**：external 合成后，`getExternalStorageState()`、`Environment.getExternalStorageDirectory()`、`MediaStore` 的路径一致性都要跟上，否则会出现"Context 给假路径、Environment 给另一条"的分裂。

---

## 六、多实例：这是唯一的硬约束

```kotlin
fun syntheticDataDir(packageName: String): String = "/data/user/0/$packageName"
```

**不含 `instanceId`。** 所以：

| 场景 | 结果 |
| --- | --- |
| 只装一个 via | `/data/user/0/mark.via` → `<sandbox_A>/` ✅ |
| **同一个 via 装两份（双开）** | 两个实例都映射 `/data/user/0/mark.via` → **规则冲突，后注册覆盖前者** ❌ |

**规则表是 `ConcurrentHashMap<String, String>`（key = from 前缀）** → 同前缀只能有一个目标 → **后注册的赢**，先注册的实例**所有 I/O 跑到另一个实例的沙箱**。这是**数据损坏级别**的问题。

### 三个解法（按推荐度）

| 方案 | 形态 | 优点 | 缺点 |
| --- | --- | --- | --- |
| **A. 按 slot 映射到不同 user id** | slot 0 → `/data/user/0/<pkg>`<br>slot 1 → `/data/user/10/<pkg>`<br>slot 2 → `/data/user/11/<pkg>` | **每个实例独立、形态完全合法**（Android 原生多用户就是 `/data/user/<N>/`） | 需要确认 app 不会因为"user 10"而行为异常 |
| B. 只对首个实例启用 pathFidelity | slot 0 假路径，其他实例走默认 | 改动最小 | 双开时第二个实例仍暴露 |
| C. 路径里混入不可见字符 | `/data/user/0/<pkg>\u200b` | — | 太脏，且会破坏路径比较 |

**方案 A 最干净**：`/data/user/<N>/<pkg>` 是 Android 10+ 的真实形态（工作资料就是 user 10+），
而且**天然按实例隔离**。需要 `syntheticDataDir(pkg, slotIndex)` 并且规则按 slot 注册。

**风险**：`/data/user/10/` 目下若宿主真有工作资料，`File.exists` 会看到真实目录 —— 但我们 hook 会拦下来，所以实际看到的是沙箱。只是在 **binder 传出去**（`FileProvider`、`Intent` 里的 `file://`）时，系统侧解析的是真实路径，可能拿到宿主数据。**这是所有 pathFidelity 方案的共同风险，需要单独评估。**

---

## 七、我建议的设计

**分三层，每层独立开关，逐层验证：**

| 层 | 内容 | 风险 | 建议 |
| --- | --- | --- | --- |
| **L1 data 目录** | `getFilesDir`/`getCacheDir`/`getDatabasePath`/`getDir`/`getNoBackupFilesDir` | 低（v135 已在这一个包上跑过） | **先在 via 上开，验证 `file://` MIME** |
| **L2 `getDataDir()`** | 单独开关 | 中（WebView 用它判断 file:// 归属，**但按第二节推理可能是解药**） | **与 L1 一起开，读 `getDataDir -> ...` 日志确认** |
| **L3 external 目录** | `getExternalFilesDir`/`getExternalCacheDir`/`getObbDir`/`getExternalMediaDirs` | 中（要让 `Environment`/`MediaStore` 一致） | **L1/L2 验证通过后再做** |

**多实例**：与 L1 同时做，否则双开场景会数据损坏。

---

## 八、需要你决定的三个问题

1. **`CM_PACKAGE` 这个白名单，是保留「定向对抗」的语义，还是改成「默认全开 + 可关」？**
   - 我倾向 **全开 + 每 app 可关**（通过 `DebugFlags` 或白/黑名单），因为「定向对抗」这种设计一旦 app 更新就失效。

2. **多实例用方案 A（`/data/user/<10+slot>/<pkg>`）还是方案 B（只有首个实例保真）？**
   - 我倾向 **A**，但需要先确认 app 对 `user 10` 是否敏感。

3. **`getExternalStorageDirectory()` / `Environment` 系列要不要一起合成？**
   - 如果只合成 `Context` 一侧，会出现"Context 说 `/storage/emulated/0/Android/data/<pkg>`、Environment 说 `/storage/emulated/0`"的分裂，**应用可能因此困惑**。要一起做还是先不做？

---

## 九、一句话

> **`pathFidelity` 不是「要不要开」的问题，而是「它只覆盖了 data 目录，
> external 完全没覆盖，而且不含 instanceId」这三个缺口必须先补，
> 否则开了也只是把暴露从 data 挪到 external，还多一个双开数据损坏的风险。**
