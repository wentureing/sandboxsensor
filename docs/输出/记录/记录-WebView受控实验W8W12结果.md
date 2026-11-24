# 记录：WebView 受控实验 W8–W12 结果（探针 v1.10，决定性）

- 日期：2026-11-24 19:09（**手机时间**，设备比电脑快约 1 分 51 秒）
- 容器：`1.0.0-c00f1cb`（含 `2fc764d` WebView 回退，Via 可正常启动）
- 探针：v1.10（`probe-3980ba3-arm64-v8a.apk`，versionCode=11）
- 沙箱实例：`com.sandbox.probe_1795518539595`（卸载重装容器后新建）
- 结果文件：`<沙箱>/data/com.sandbox.probe_1795518539595/files/wvprobe-result.txt`

> 本文件**取代** `记录-WebView受控实验W0W7结果.md` 作为最新事实来源。
> W0–W7 的旧结果只在"对比"时引用。

---

## 一、假设排除表（vs `计划-WebView修复.md` 的 C4）

| 假设 | 结果 | 证据 |
| --- | --- | --- |
| **C4-a：WebView 的 Context 不对** | ❌ **排除** | W8 `SAME_CONTEXT=true`、`SAME_PACKAGE=true` |
| **C4-a'：Assets 不是同一个** | ❌ **排除** | W12 `SAME_ASSETS=true`，两边都能 `open(wvprobe.html)` |
| **C4-b：hook 改写路径导致扩展名丢失** | ❌ **排除** | W2/W4 拦截器看到的 URL 扩展名 `.html` 完整 |
| **C4-c：Java 层 MIME 表被破坏** | ❌ **排除** | W9 `text/html`、W10 `text/html` |
| **C4-d：WebView 创建失败** | ❌ **排除** | W0 成功，UA 正常（Chrome/126.0.6478.71） |
| **C5：Chromium 自己读 `file://` 时 MIME 判定错** | ✅ **成立** | W2/W4 显示源码 vs W11 主动返回 `text/html` 就正常 |

---

## 二、完整实验数据（原样）

### W9 / W10：Java 层 MIME 表 —— 正常

```
W9  MimeTypeMap.getMimeTypeFromExtension("html") = text/html
W9  getFileExtensionFromUrl(file:///a/b/x.html)  = html
W10 URLConnection.guessContentTypeFromName("x.html") = text/html
W10b guessContentTypeFromName("x.txt")               = text/plain
```

**结论**：Java 侧的 MIME 映射完全正常，`.html -> text/html` 有值。

### W5：`getAllApkPaths()` —— 这次 n=4（异常！）

```
W5 getAllApkPaths() n=4
W5   /data/user/0/com.nextvm.app/files/virtual/apks/com.sandbox.probe_1795518539595.apk exists=true
W5   /data/resource-cache/com.android.systemui-neutral-SAbe.frro exists=true
W5   /data/resource-cache/com.android.systemui-accent-LZ5k.frro exists=true
W5   /data/resource-cache/com.android.systemui-dynamic-M0Kl.frro exists=true
```

**对比 v1.9（同一台设备，上一次运行）**：

```
W5 getAllApkPaths() n=10    ← 含 WebViewGoogle64.apk 和 TrichromeLibrary64.apk
```

**⚠️ 两次差别**：v1.9 那次是"容器已跑过一段时间"，v1.10 这次是"卸载重装后首次"。

**重要线索**：
> `ApplicationInfo.getAllApkPaths()` 在 **WebView 尚未初始化时**不包含 WebView 的 APK。
> 说明 AOSP 的 `WebViewFactory` 是在**首次用到 WebView 时**才把 WebView 的 APK 关联到这个
> `ApplicationInfo` 上（或通过 `createApplicationContext` 另建了一个 context）。

**这也解释了 W6 的 `webkit/` 只有 1 个 png**：

```
W6 list("webkit") -> 1 entries
W6   webkit/android-weberror.png
W6 list("") -> 6 entries
```

**WebView 的资源并没有加到"探针的 AssetManager"里** —— 它用自己的。
（W0/W1 证明 WebView 能正常工作，所以它的资源在它自己的 context 里。）

### W7：`0x02` 域资源 —— 仍不可用

```
W7 FAIL: getInteger(0x020c0006) -> NotFoundException
W7 probe: getValue(0x020c0006) -> NotFoundException
W7 FAIL: getInteger(0x020c0000) -> NotFoundException
W7 probe: getValue(0x020c0000) -> NotFoundException
```

**注意**：这是**探针自己的 Resources**，不是 WebView 的。
**W7 只能说明"guest 的 Resources 里没有 0x02 域"，这本来就是预期的**（`0x02` 是 shared library 域）。

**要验 popup 崩溃，必须让真实的 WebView 触发 `onCreateActionMode`（长按文字）。**

### W0：WebView 创建 —— 成功（主线程修复生效）

```
W0 ok: WebView created on main thread
W0 settings ua=Mozilla/5.0 (Linux; Android 15; V2463A Build/AP3A.240905.015.A2_V000L1; wv)
             AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/126.0.6478.71 Mobile Safar...
```

### W8 / W12：Context 与 Assets —— 完全正常

```
W8 webView.getContext().getPackageName() = com.sandbox.probe
W8 this.getPackageName()                 = com.sandbox.probe
W8 context class = com.sandbox.probe.WebViewProbeActivity identityHash=154918564
W8 this identityHash = 154918564
W8 SAME_CONTEXT = true
W8 SAME_PACKAGE = true

W12 this.getAssets() identityHash = 158329665
W12 wv.context.getAssets() identityHash = 158329665
W12 SAME_ASSETS = true
W12 this.getAssets().open(wvprobe.html) OK, available=162
W12 wv.context.getAssets().open(wvprobe.html) OK
```

### 五个加载实验 —— **核心对照**

```
W1 INTERCEPT url=data:text/html;charset=utf-8;base64, method=GET
W1 url=about:blank -> finished body="WVPAGE_OK\n\nhello from probe"

W2 INTERCEPT url=file:///data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795518539595/files/wvprobe.html method=GET
W2 url=file:///.../wvprobe.html -> finished body="\u003Chtml>\u003Cbody>\u003Ch1 id='h'>WVPAGE_OK\u003C/h1>\u003Cp>hello from probe\u003C/p>\u003C/body>\u003C/html>"

W3 INTERCEPT url=file:///android_asset/wvprobe.html method=GET
W3 url=file:///android_asset/wvprobe.html -> finished body="EMPTY"

W4 INTERCEPT url=file:///data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795518539595/files/wvprobe_w4.html method=GET
W4 url=file:///.../wvprobe_w4.html -> finished body="\u003Chtml>\u003Cbody>\u003Ch1 id='h'>WVPAGE_OK\u003C/h1>..."

W11 INTERCEPT url=file:///data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795518539595/files/wvprobe.html method=GET
W11 INTERCEPT -> 主动返回 text/html, file len=74
W11 url=file:///.../wvprobe.html -> finished body="WVPAGE_OK\n\nhello from probe"  [intercepted 1]  [W11_MODE]
```

### 三方对照表（同一文件、同一 URL、同一 WebView）

| 场景 | MIME 由谁决定 | 结果 |
| --- | --- | --- |
| **W2 / W4** | **Chromium 自己**（`FileURLLoaderFactory`） | **`text/plain`** → 显示源码 |
| **W11** | **容器**（`WebResourceResponse("text/html", "utf-8", stream)`） | **`text/html`** → 正常渲染 |
| **W9 / W10** | Java 层 `MimeTypeMap` / `URLConnection` | **`text/html`（正确）** |

**结论**：
> **Java 层的 MIME 映射是对的，但 Chromium 读 `file://` 时不用它。**
> **容器只要补上 MIME 就能修好（W11 已验证）。**

---

## 三、W3 的新发现：`EMPTY` 而不是 error

**v1.9 的 W3**（无 `allowFileAccess` 重试）：
```
W3 url=file:///android_asset/wvprobe.html -> error code=-1 desc=net::ERR_FILE_NOT_FOUND
```

**v1.10 的 W3**（`shouldInterceptRequest` 返回 null，让 WebView 自己处理）：
```
W3 url=file:///android_asset/wvprobe.html -> finished body="EMPTY"
```

**差异**：
- v1.9 报了 `ERR_FILE_NOT_FOUND`
- v1.10 **没有报错，只是 body 空**

**说明**：`android_asset` 在容器里**不是"文件不存在"，而是"打开了一个空的东西"**。

**可能的机制**：容器 hook 了 `AssetManager.openNonAsset` 相关路径，返回空流。
**待查**：`AAPT`/`AssetManager` 的 `openNonAsset` 是否被 hook。

---

## 四、下一步调查方向（按优先级）

### D1：Chromium 的 `file://` MIME 判定为什么错（**最高优先级**）

**已知**：
- 扩展名 `.html` 完整（W2/W4 的 URL 确认）
- Java 层 MIME 表正常（W9/W10）
- **Chromium 仍判成 `text/plain`**

**候选机制**：

| # | 可能 | 怎么查 |
| --- | --- | --- |
| **M1** | 容器 hook 了 `stat`/`lstat`，Chromium 拿不到文件信息 → 落回默认 `text/plain` | 查 `native-hook.cpp` 是否 hook `stat`，以及 hook 后的返回值 |
| **M2** | 容器 hook 了 `open`/`read`，Chromium 读前 512 字节嗅探失败 → 落回 `text/plain` | 同上 |
| **M3** | Chromium 的 MIME 表（`mime_util.cc` 里的静态表）在容器环境下加载失败 | 查 `libwebviewchromium` 是否有 `GOT patch skipped` 相关日志 |
| **M4** | `file://` 的实际读取走的是**被重定向后的路径**（沙箱路径），而 Chromium 按**原始路径**判扩展名 → 不匹配 | 查 `redirect_path` 是否改写了传给 Chromium 的路径 |

**M4 最值得先查**：因为我们确实 hook 了 `open`/`stat` 做路径重定向。

### D2：`android_asset` 返回空（W3）

**候选**：
- 容器 hook 了 `AssetManager.openNonAsset`
- 或 `AssetManager` 的 `addAssetPath` 让 guest assets 位置变了

### D3：`getAllApkPaths()` 的 n=4 vs n=10

**候选**：
- WebView 未初始化时不包含 WebView APK（正常行为）
- 但这也说明 AOSP 的 shared library 加载**可能没在容器里跑**

---

## 五、当前修复方案（已被 W11 验证）

### 方案 A：容器 hook `shouldInterceptRequest` 补 MIME

**已验证有效**（W11）。

**待定细节**：
1. hook `WebViewClient` 的哪个方法（`setWebViewClient` 还是 `shouldInterceptRequest` 本身）
2. 只对 `file://` 生效，还是也覆盖 `content://`、`android_asset`
3. 用户自己实现了 `shouldInterceptRequest` 时怎么合并（容器只在用户返回 `null` 时接管）

**风险**：
- 如果应用自己返回了错误的 MIME，容器要不要覆盖？
- 高频调用（每个子资源都过）

### 方案 A'：修 M1/M2/M4 的根因（更彻底）

**如果 M4 成立**（hook 改写了路径导致 Chromium 判错），那么修 hook 比补 MIME 更彻底 —— **一次修好所有走 `file://` 的场景**。

**所以优先查 M4。**
