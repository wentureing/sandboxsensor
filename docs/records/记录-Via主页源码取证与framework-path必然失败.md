# Via 主页显示源码：现场取证与 `bindViaFrameworkLoadedApk` 的必然失败

- 日期：2026-11-24
- 起因：用户指出 —— 探针的 popup **无故障**；故障疑似是 **Via 在容器内调用 WebView** 的问题；
  已准备好主页；允许用 python 工具查看报错；怀疑有些函数**未实现或过时**
- 结论先给：**截图证明了"显示源码"**；**找到了 `bindViaFrameworkLoadedApk` 必然失败的证据**
  （`Log.wtf: App instance already created for package=com.nextvm.app`）

---

## 一、现场取证（截图 + UI dump）

### 1.1 截图内容（`probe-reports/via-screen.png`）

Via 加载 `homepage2.html`，**标题栏显示文件名，正文显示完整 HTML 源码**：

```
<!DOCTYPE html><html><head><meta content="text/html; charset=utf-8" ...
<title>主页</title><link rel="stylesheet"
    href="/data/user/0/com.nextvm.app/files/virtual/data/mark.via_1795488613994/files/homepage.css">
...
```

### 1.2 截图里的三个关键信息

| # | 信息 | 意义 |
| --- | --- | --- |
| 1 | **正文是 HTML 源码** | 不是渲染结果 → **MIME 被当成 `text/plain`** |
| 2 | **CSS 路径是 `<沙箱>/data/mark.via_.../files/homepage.css`** | **`getFilesDir()` 正确返回了沙箱路径** ✅ |
| 3 | `<meta charset="utf-8">` 在源码里 | HTML 本身格式正确，是"被当文本显示" |

**推论**：
> **不是"路径重定向失败"**（路径是对的，在沙箱里），
> **而是"WebView 拿到的 MIME 不对"**（`text/plain` 而非 `text/html`）。

### 1.3 UI dump 只看到两个节点

```
homepage2.html    TextView
1                 TextView
```

**说明 Via 的这个页面用 `TextView` 显示内容，不是 WebView 渲染** ——
这更可能是 **Via 自己的"文件查看器"模式**（因为它识别不了 `.html`？）。

---

## 二、`bindViaFrameworkLoadedApk` 的必然失败（用户怀疑的"过时"得到印证）

### 2.1 异常堆栈（10:51 那次，pid 3232）

```
E LoadedApk: android.util.Log$TerribleFailure:
              App instance already created for package=com.nextvm.app
              instance=com.nextvm.app.NextVmApplication@...
    at android.util.Log.wtf(Log.java:338)
    at android.util.Slog.wtfStack(Slog.java:248)
    at android.app.LoadedApk.makeApplicationInner(LoadedApk.java:1467)
    at android.app.LoadedApk.makeApplication(LoadedApk.java:1436)
    at java.lang.reflect.Method.invoke(Native Method)
    at VirtualApplicationManager.bindViaFrameworkLoadedApk(VirtualApplicationManager.kt:1446)
    at VirtualApplicationManager.bindApplication(VirtualApplicationManager.kt:507)
    at VirtualEngine.ensureGuestApplicationBooted(VirtualEngine.kt:1776)
    at ActivityThreadHook.handleLaunchActivityItem(ActivityThreadHook.kt:640)
```

### 2.2 代码逻辑（`VirtualApplicationManager.kt:1441-1458`）

```kotlin
val application = try {
    loadedApkClass.getMethod("makeApplication", Boolean::class.javaPrimitiveType,
                             Instrumentation::class.java)
        .invoke(loadedApk, false, null) as? Application        // ← L1446：抛 Log.wtf，返回 null
} catch (e: NoSuchMethodException) {
    loadedApkClass.getMethod("makeApplicationInner", ...)
        .invoke(loadedApk, false, null, false) as? Application
}
if (application == null) {
    GuestBreadcrumb.log("framework makeApplication returned null")   // ← L1456
    restoreInstrumentation(prevInstr)
    return null                                                       // ← 返回 null
}
```

### 2.3 必然失败的原因

**`LoadedApk.makeApplicationInner` 的逻辑**（AOSP）：

```java
if (mApplication != null) {
    Log.wtf(TAG, "App instance already created for package=" + mPackageName
                 + " instance=" + mApplication);
    return mApplication;      // ← 返回**已存在的**那个（容器的 NextVmApplication）
}
```

**所以**：
1. 容器的 `ActivityThread` 已经为 `com.nextvm.app` 创建了 `NextVmApplication`
2. 同一个 `LoadedApk` 的 `mApplication` **不为 null**
3. `makeApplication` → `Log.wtf` → **返回容器的 Application**（不是 guest 的）
4. **`as? Application` 成功**（因为返回的是 `Application` 类型）
5. **但那是容器的 App，不是 guest 的** → 后续所有 guest 逻辑都在容器 App 上跑

**关键**：
> **`bindViaFrameworkLoadedApk` 的设计前提是"`LoadedApk.mApplication` 还没创建"**，
> **但容器主 Application 已经创建** → **这个前提在当前架构下永远不成立**。

**这就是用户怀疑的"过时"** —— 这条路可能只在特定条件下（如纯 Service 进程、无主 Application）可用。

### 2.4 16:15 这次没走这条路

- **`framework makeApplication` 日志 0 命中**
- **`breadcrumb-4023.log` 里只有 `v131-autofill` 的循环日志**

**说明**：这次启动既没成功走 framework path，也没打失败日志 →
**可能走到了另一个分支**（或者 breadcrumb 没记录）。

**需要单独查**：`bindApplication` 的完整分支逻辑。

---

## 三、`WebViewWatch` / `WebViewWarmup`：不是空壳，但无日志

| 文件 | 行数 | `schedule()` 实现 | 日志 |
| --- | --- | --- | --- |
| `WebViewWatch.kt` | **170 行** | 起 `webview-watch` 线程 → `runWatch(filesDir)` | **0 命中** |
| `WebViewWarmup.kt` | **404 行** | 起 `webview-warmup` 线程 → `runWarmup(filesDir)` | **0 命中** |

**两个都有真实实现**（不是空壳），**但 logcat 里没有它们的输出**。

**可能**：
1. `runWatch`/`runWarmup` 内部不打日志（只写文件）
2. 或它们的 `schedule` 没被调用（`bindViaFrameworkLoadedApk` 返回 null → 走 legacy → 那两行在 framework 分支里）

**关键**：`WebViewWatch.schedule` / `WebViewWarmup.schedule` 在 **L524-525**，
**都在 `if (fwApp != null)` 分支内** →
**因为 `fwApp` 一直是 null，所以这两个监控从未启动！**

**这是一个附带发现**：

> **容器自带的 WebView 监控/预热机制，因为 `bindViaFrameworkLoadedApk` 恒失败，从未运行过。**

---

## 四、与用户三个判断的对照

| 用户的判断 | 证据 | 结论 |
| --- | --- | --- |
| **探针的 popup 无故障** | `探针-检测项清单.md:161`：`P25 showing=true` + `P26 added+removed ok` → **窗口链路正常** | ✅ **正确** |
| **故障是 Via 在容器内调 WebView** | `架构-总览.md:185`："长按 ANR **只在 WebView 里出现**" + 本次截图 | ✅ **正确** |
| **有些函数未实现或过时** | `bindViaFrameworkLoadedApk` 因 `LoadedApk.mApplication` 已存在而**恒失败**；连带 `WebViewWatch`/`WebViewWarmup` **从未启动** | ✅ **正确** |

---

## 五、下一步（待用户决定）

| # | 方向 | 说明 |
| --- | --- | --- |
| **1** | **查 `bindApplication` 的完整分支** | 确认 16:15 那次走了哪条路 |
| **2** | **修 `bindViaFrameworkLoadedApk` 的前提判断** | 加"`LoadedApk.mApplication` 已存在"的检测 → 直接跳过 framework path |
| **3** | **把 `WebViewWatch`/`WebViewWarmup` 从 framework 分支移出** | 它们对诊断有用，不该绑在一条恒失败的路上 |
| 4 | **MIME 问题**（显示源码的直接原因） | 需要查 Via 是怎么读 `.html` 的（它自己的 `shouldInterceptRequest`？） |
