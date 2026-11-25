# 架构：WebView 的假路径必须换成真路径

- 日期：2026-11-25
- 状态：**已执行**（`contentPathsUseReal = false`）
- 代价：guest 自己看到的内容目录含宿主包名（**暂时接受，暂不修复**）
- 未来：计划内置 WebView，届时可以彻底解决

---

## 一、问题：via 主页空白，WebView 打不开

via 主页由 WebView 加载 `file://` 本地 HTML：

```
via 拼路径：getFilesDir() + "homepage2.html"
  pathFidelity 开启时 getFilesDir() 返回 /data/user/0/mark.via/files     ← 假路径
拼出：       file:///data/user/0/mark.via/files/homepage2.html
```

**这个文件在真机上不存在**，真实文件在：

```
/data/user/0/com.nextvm.app/files/virtual/data/<inst>/files/homepage2.html   （6333 字节，合法 HTML）
```

---

## 二、为什么"路径翻译"救不了 WebView —— 两层原因

### 2.1 渲染进程根本不在我们的 hook 范围

实测进程表：

```
13698  uid=10373  mark.via
18326  uid=99613  com.google.android.webview:sandboxed_process0:org.chromium.content.app.SandboxedProcessService0:0
 4308  webview_zygote                                   ← 18326 的父进程
```

- WebView 的渲染跑在 **`sandboxed_process0`**，父进程是 **`webview_zygote`**（不是 `mark.via`）
- 该进程 **`uid=99613`**（Android 给 isolated 进程的独立 uid）
- 该进程的 hook 日志**完全为空** —— 没有任何 `installJavaIoHooks` / `NextVM` 记录

**结论**：路径字符串一旦交给 WebView，解析发生在 isolated 进程，**那里没有 RegisterNatives、没有 GOT patch**。

### 2.2 即使能翻译，`RegisterNatives` 也 hook 不到 `loadUrl`

- `WebView.loadUrl(String)` 是 **Java 方法**（在 boot 的 `framework.jar`）
- 它的实现走到 chromium 的 `AwContents`，但 `AwContents` 在 webview apk 的 dex 里
  **native 方法数为 0**（实测 `dexdump`）—— chromium 通过 `JNI_OnLoad` 动态注册，
  **我们拿不到那张 method table**，`RegisterNatives` 无从下手
- `shouldInterceptRequest` 按官方设计**只拦 http/https**（[WebViewAssetLoader 文档](https://developer.android.google.cn/reference/androidx/webkit/WebViewAssetLoader)），拦不到 `file://`

---

## 三、关键证据：isolated 进程**能**读宿主私有目录

探针历史上两条对照记录：

```
W18:  file:///data/user/0/com.nextvm.app/files/wvprobe18.html
      -> ERR_FILE_NOT_FOUND                          （该文件确实不存在）

W35a: file:///data/user/0/com.app.hider.master.pro.cn/gaia/data/user/0/com.sandbox.probe/files/wvprobe_bom.html
      -> WVPAGE_OK                                   （文件真实存在 -> 读到了）
```

**W35a 是 gaia 的形态**：路径落在 **hostPkg 自己的私有目录**里，isolated 进程**读得到**。

**所以**：
> **isolated 进程能读宿主私有目录 —— 前提是路径真实存在。**
> **沙箱文件的真实位置就在容器私有目录里（`/data/user/0/com.nextvm.app/files/virtual/data/<inst>/...`），
> 只要把真路径交给 WebView，就能读到。**

---

## 四、已执行的改动

`VirtualContext` 新增：

```kotlin
/** 内容目录是否用合成路径。2026-11-25 起默认 false —— 见本文档。 */
private val contentPathsUseReal: Boolean = false

private fun contentDir(sandboxDir: File, synthetic: () -> File): File =
    if (pathFidelity && !contentPathsUseReal) synthetic() else sandboxDir
```

**把所有"内容目录"改成走 `contentDir`**：

| 方法 | 之前的返回 | 现在 |
| --- | --- | --- |
| `getFilesDir()` | `/data/user/0/<pkg>/files` | **真实沙箱路径** |
| `getCacheDir()` | `/data/user/0/<pkg>/cache` | **真实沙箱路径** |
| `getCodeCacheDir()` | `/data/user/0/<pkg>/code_cache` | **真实沙箱路径** |
| `getNoBackupFilesDir()` | `/data/user/0/<pkg>/no_backup` | **真实沙箱路径** |
| `getExternalFilesDir(type)` | `/storage/emulated/0/Android/data/<pkg>/files[/type]` | **真实沙箱路径** |
| `getExternalCacheDir()` | `/storage/.../data/<pkg>/cache` | **真实沙箱路径** |
| `getObbDir()` | `/storage/.../obb/<pkg>` | **真实沙箱路径** |
| `getExternalMediaDirs()` | `/storage/.../media/<pkg>` | **真实沙箱路径** |
| **`getDataDir()`** | `/data/user/0/<pkg>` | **保留合成路径**（不变） |
| **`ApplicationInfo.dataDir`**（PackageManagerProxy） | 合成路径 | **保留**（不变） |

### 为什么保留 `getDataDir()` 的合成路径

两件事分开看：

| 需求 | 用什么 | 为什么 |
| --- | --- | --- |
| **文件真的能被读写**（含 WebView） | **真路径** | isolated 进程只认真实存在的路径 |
| **让 guest 觉得"我装在真机上"**（反双开检测） | **`dataDir` 合成路径** | 这类检测看的是 `getDataDir()` / `ApplicationInfo.dataDir`，不看 `getFilesDir()` 的子路径 |

**所以**：`contentPathsUseReal = false` 让内容目录用真路径（功能正常），
`getDataDir()` 仍返回 `/data/user/0/<pkg>`（伪装仍在）。

---

## 五、代价（明确接受，暂不修复）

**guest 自己**看到的 `getFilesDir()` 是：

```
/data/user/0/com.nextvm.app/files/virtual/data/<inst>/files
```

→ **含宿主包名 + `virtual/data` + instanceId**。任何打印这个路径的 guest 都能看出自己在容器里。

**为什么暂不修**：

1. **WebView 的 isolated 进程无法被欺骗** —— 这是 Android 进程模型决定的，不是我们的实现问题
2. **能彻底修的路只有两条**，都不适合现在做：
   - **hook `WebView.loadUrl`**（改 `ArtMethod` entry）：需要维护 per-API 的 `ArtMethod` 偏移表，与"精简"理念冲突
   - **拦截 `file://` 请求**：`shouldInterceptRequest` 官方不支持 `file://`
3. **用户已确认**：WebView 看见真路径的缺陷**暂不修复**

---

## 六、via 的行为记录（反编译实测）

| via 的 WebView 调用 | 次数 | 位置 |
| --- | --- | --- |
| **`WebView.loadUrl(String)`** | 2 | **`Ld9/q;.i3`**（持有 `Ld9/q;.u0:Landroid/webkit/WebView;` 直调）<br>`Lc8/ua;.n1`（经包装类 `Lt4/b;.loadUrl`） |
| **`shouldInterceptRequest`** | 1 | **`Ld9/q$c`**（两个重载：`WebResourceRequest` 版 + `String` 版）—— via 用它做请求拦截，但**拦不到 `file://`** |
| `setAllowFileAccess` | 2 | — |
| `setAllowFileAccessFromFileURLs` | 1 | — |
| `setAllowUniversalAccessFromFileURLs` | 1 | — |
| `file://` 字面量 | 2 | `file://`（拼接用）、`file:///android_asset/logo.svg` |

**关键**：via **已经开启了 `setAllowFileAccess` + `setAllowFileAccessFromFileURLs` +
`setAllowUniversalAccessFromFileURLs`** —— 所以 WebView 层面**不阻止** `file://`，
问题**纯粹是文件不存在**。

**这意味着**：只要交给 WebView 的是**真实存在的路径**，via 的 `file://` 加载就能成功，
不需要改 via 的任何设置。

---

## 七、未来：内置 WebView

用户已提出「未来有计划内置 WebView」。届时：

| 现状（用系统 WebView） | 内置后 |
| --- | --- |
| 渲染在 `com.google.android.webview:sandboxed_process0`（独立 uid，无 hook） | 渲染进程由容器自己 fork，**可以装上 hook** |
| 只能给真实路径 | 可以给合成路径，**彻底不暴露** |
| 内容目录必须用真路径（本次改动） | 可以改回合成路径 |

**所以本次的 `contentPathsUseReal = false` 是过渡方案** —— 内置 WebView 之后应该改回 `true`
并验证主页能正常加载。

---

## 八、验证方法（装包后）

| 判据 | 期望 |
| --- | --- |
| `Dbg/ctx: getFilesDir -> ...` | **含 `/data/user/0/com.nextvm.app/files/virtual/data/`**（真路径） |
| `Dbg/ctx: getDataDir -> ...` | **仍是 `/data/user/0/mark.via`**（合成路径保留） |
| via 主页 | 不再空白，能渲染 `homepage2.html` |
| `canonicalize0 [synth]` 日志 | 应该减少（因为内容目录不再走合成路径，命中规则的机会变少） |
