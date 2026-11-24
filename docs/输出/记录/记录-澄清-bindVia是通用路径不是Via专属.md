# 命名澄清：`bindViaFrameworkLoadedApk` 里的 "Via" 是介词"通过"，不是 Via 浏览器

- 日期：2026-11-24
- 起因：用户指出 —— **`bindViaFrameworkLoadedApk` 不一定是 Via 的专属兼容，这里 via 可能只是名字一部分**
- **结论：用户完全正确。这是"bind **via** framework LoadedApk"（通过 framework 的 LoadedApk 来 bind），对所有应用通用**

---

## 一、代码里的证据

### 1.1 进入条件：**只看 `frameworkLoadedApk != null`，与包名无关**

```kotlin
// VirtualApplicationManager.kt:502-507
// B2: framework-native bind — when a framework LoadedApk is
// available for this guest, the Application is created and
// attached by the framework itself instead of being
// hand-assembled onto the host's (patched) LoadedApk.
if (frameworkLoadedApk != null) {
    val fwApp = bindViaFrameworkLoadedApk(
        instanceId = instanceId,
        packageName = packageName,
        loadedApk = frameworkLoadedApk,
        ...
```

**注释原文**：
> **"B2: framework-native bind"**
> "when a framework LoadedApk is available **for this guest**"
> "the Application is created and attached **by the framework itself** instead of being hand-assembled onto the host's (patched) LoadedApk"

**所以**：
- **`via` = 介词"通过"**
- **含义：用 framework 的 LoadedApk 来完成 bind（而不是手工组装）**
- **对所有 `frameworkLoadedApk != null` 的 guest 都走**

### 1.2 真正的"包名硬编码"只有一处，而且**只影响诊断**

```kotlin
// VirtualApplicationManager.kt:496-498
if (packageName == "com.greenpoint.android.mc10086.activity") {
    startV154InitTrace()      // ← 只启动一个诊断 trace，不影响 bind
}
```

**`com.greenpoint.android.mc10086.activity`** = 中国移动营业厅（之前 `v141` 日志里的同一个包）。

**注释原文**（L491-495）：
> "a background filter pass extracts only device-identity reads into v155-init-filtered.log... **A failed start/stop/filter never touches the bind.**"

**所以**：**唯一针对特定包的代码不影响 bind 本身**。

---

## 二、我之前的误读（第 5 次同类错误）

| 我说的 | 实际 |
| --- | --- |
| "`bindViaFrameworkLoadedApk` 是上游为 Via 写的兼容函数" | **是"bind 用 framework LoadedApk"的通用路径** |
| "Via 是特殊 case" | **Via 走的是和所有应用一样的路** |
| "方案 B 需要考虑其他软件怎么跑" | **方案 B（framework-native bind）本来就是所有应用的路径** |

**错误模式**：**从函数名里的 "Via" 猜出"Via 浏览器"** —— 和之前"从搜索结果为空猜死代码"、"从 `Log.wtf` 猜失败"是同一类：**用局部信号代替完整验证**。

---

## 三、两条 bind 路径的正确理解

| 路径 | 代码 | 机制 | 何时用 |
| --- | --- | --- | --- |
| **B2: framework-native bind**（主路径） | `bindViaFrameworkLoadedApk` | 为 guest 建一个**独立的 `LoadedApk` clone**（`mApplication=null`）→ 调 `makeApplication` → **框架自己创建 Application** | `frameworkLoadedApk != null`（**默认都是**） |
| **legacy bind**（fallback） | `createApplication` + `attachBaseContext` | **手工 `new` Application 实例** → 手动 `attach` → 组装到宿主的（已 patch）LoadedApk | `createFrameworkLoadedApk` 返回 null 时 |

**关键对比**：

| 项 | B2（framework-native） | legacy |
| --- | --- | --- |
| Application 由谁创建 | **框架的 `makeApplication`** | 容器反射 `newInstance` |
| 用哪个 LoadedApk | **guest 的 clone（`mApplication=null`）** | 宿主的（已 patch `mApplication`） |
| 是否需绕过 `makeApplication` | **不需要**（clone 的 `mApplication` 是 null） | 它本来就绕过了 |
| 当前是否是主路径 | ✅ **是** | ❌ fallback |

**所以"方案 B（绕过 `makeApplication` 自己 new）"= legacy path**，
**而它已经是 fallback，且比主路径更"手工"** → **不需要把它提为主路径**。

**用户之前问的"其他软件怎么跑，能不能一样"** → **答案：完全一样，全都走 B2。**

---

## 四、`frameworkLoadedApk` 是哪来的

breadcrumb 里（`breadcrumb-28055.log`）：

```
[+0ms] createFrameworkLoadedApk call pkg=mark.via appInfo.packageName=mark.via
              appInfo.className=mark.via.BrowserApp
              appInfo.sourceDir=.../apks/mark.via_1795488613994.apk
[+0ms] createFrameworkLoadedApk result loadedApk=#34683083 mPackageName=mark.via
              mApplication=null
              mPackages[guest]=#34683083
              mPackages[host]=#145045672
[+38ms] replaceClassLoader done pkg=mark.via target=#245389053
              mPackages[guest]=#245389053 mPackages[host]=#145045672
```

**关键**：
- **`mPackages[host]=#145045672`** 是**容器的 LoadedApk**
- **`mPackages[guest]=#245389053`**（replaceClassLoader 后）是**guest 的 clone**
- **两个对象分开** → 所以 `makeApplication` 在 guest clone 上能成功

**`Log.wtf` 的来源**：
- `mPackageName` 被 patch 成 `com.nextvm.app`（为了 `AppOpsManager.checkPackage`）
- **但那是 guest clone 的 `mPackageName` 字段**，`mApplication` 仍是 null
- **`Log.wtf` 里的 `instance=NextVmApplication@ada75ed` 说明它读到了容器的 App** →
  **可能 AOSP 的 `makeApplicationInner` 会检查 `ActivityThread.mInitialApplication`**，或者**另有代码在容器的 LoadedApk 上调了 `makeApplication`**

**这一点仍需查证**（不是本轮结论）。

---

## 五、对其他四项要求的回答

### 5.1 `link()` 问题：**真实存在，但不紧迫**

**实测**：
```
$ ls -la files/virtual/.apk_guard
drwx------ 2 u0_a321 u0_a321 . .apk_guard     ← 目录存在
drwx------ ... .
drwx------ ... ..
                                               ← 空！从没有过硬链接
```

**所以 `link()` 一直失败** ✓ **问题真实存在**

**但不紧迫**：
| 项 | 说明 |
| --- | --- |
| 只影响"会删自己 APK 的 SDK" | 注释提到 `ZWRisk.setup()`（一个风控 SDK） |
| **Via 不删** | breadcrumb 里没有 v81 restore 记录 |
| 普通应用不删 | — |
| **平台限制** | Android 10+ 的 `untrusted_app` 对 `link()` 有限制（[StackOverflow: Is Android M not allowing hard links?](https://stackoverflow.com/questions/32365690/is-android-m-not-allowing-hard-links)） |

**结论**：**记录为"已知限制"，不做修复**（除非遇到具体应用崩）。

**如果要做**：用 `copy` 代替 `link`（占双份空间，但可行）。

### 5.2 `mAudioService`：应做**版本兼容**，不是消除

**方向**（用户要求）：
- 不删反射
- 加**多版本 fallback**：`mAudioService` → `mAudioServer` → `sAudioService` → `getService()`

**工具建议**（待评估）：
| 工具 | 用途 | 评价 |
| --- | --- | --- |
| **`ReflectionUtils`（自己写）** | 多候选字段名 + 类型兼容 | 简单可控 |
| `HiddenApiBypass`（LSPlant 作者） | 绕过 hidden API 限制 | 项目已经在用 LSPosed 的 bypass |
| `FreeReflection` | 同上 | 更简单但侵入大 |

**建议**：**自己写一个 `findFieldCompat(clazz, "mAudioService", "mAudioServer", "sAudioService")`**，
配合现有的 `findField`（`VirtualApplicationManager` 里已有）。**不引入新依赖**。

### 5.3 1/3/4 的"完整代码路径调查"（**不能基于片段修复**）

| # | 任务 | 需要完整调查什么 |
| --- | --- | --- |
| **1** | 修双轨沙箱 | ①`SandboxManager` 的 **25 个公共方法全部**指向哪个根 ②它们的**所有调用方**（`VirtualContext` 8 个 + `CmdReceiver` 3 个 + UI 若干）③**`VirtualEngine` 自己算路径的地方**与 `SandboxManager` 是否冲突 |
| **3** | P0-3 主进程不装 hook | ①`initNativeHooks` 的**所有调用方** ②主进程里**依赖 hook 的全部代码路径**（`hidePath`/`addPathRedirection`/`spoofIdentity` 的调用者）③`CmdReceiver` 的 8 条命令是否都走真实路径 |
| **4** | P0-4/P0-5 隐藏容器 | ①`PackageManagerProxy` 的**所有暴露包名的入口**（不止 5 个）②`is_path_hidden` 的**所有使用点**（6 处）③`opendir`/`readdir` 的现有实现 |

**结论**：**这三项都必须先做"完整代码路径调查"，落盘 `docs/changes/` 计划，再动手。**

---

## 六、修正记录

| 文档 | 需要改的地方 |
| --- | --- |
| `records/记录-Via主页源码取证与framework-path必然失败.md` | 标题的"Via"应理解为"通过"；"必然失败"结论已作废 |
| `records/记录-Via启动全链路取证-五个真实问题.md` | 同上 |
| `records/记录-修正-framework-path实际成功-与计划整理.md` | 补"via=通过"的澄清 |
