# 记录：WebView 受控实验 W0–W7 结果（决定性数据）

- 日期：2026-11-24 18:43（**手机时间**，设备比电脑快约 1 分 50 秒）
- 容器版本：`1.0.0-c00f1cb`（**含 `2fc764d` WebView 接线回退**）
- 探针版本：v1.9（`probe-bddb6aa-arm64-v8a.apk`，versionCode=10）
- 沙箱实例：`com.sandbox.probe_1795516976639` + `mark.via_1795516889549`
- 结果文件：`<沙箱>/data/com.sandbox.probe_1795516976639/files/wvprobe-result.txt`

---

## 零、最重要的两件事

### 1. **回退生效，Via 不再 boot loop**

```
18:42:27.409 I BootCrumb: [+10083ms] [main] v131-autofill per-view mark.via.Shell total=34
18:42:32.414 I BootCrumb: [+15087ms] [main] v131-autofill per-view mark.via.Shell total=34
18:42:37.419 I BootCrumb: [+20092ms] [main] v131-autofill per-view mark.via.Shell total=34
18:42:42.424 I BootCrumb: [+25097ms] [main] v131-autofill per-view mark.via.Shell total=34
18:42:47.429 I BootCrumb: [+30102ms] [main] v131-autofill per-view mark.via.Shell total=34
前台: topResumedActivity=com.nextvm.app/.stub.P0$SingleTask01
FATAL EXCEPTION: 0    am_proc_died: 0
```

**对比修复前**：每 0.5 秒换一个 pid、卡在 `onActivityStarted`、`InflateException: layout/p line #48`。

**结论**：`2fc764d`（去掉 `ensureWebViewResources` 接线）**确实修好了 boot loop**。

### 2. **"显示源码"不是读不到文件，是 MIME 判定问题**

```
W1 url=about:blank -> finished body="WVPAGE_OK\n\nhello from probe"
W2 url=file:///data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795516976639/files/wvprobe.html
   -> finished body="\u003Chtml>\u003Cbody>\u003Ch1 id='h'>WVPAGE_OK\u003C/h1>\u003Cp>hello from probe..."
```

**关键对比**：

| 项 | W1（loadDataWithBaseURL） | W2（loadUrl file://） |
| --- | --- | --- |
| 数据来源 | 内存里的字符串 | **沙箱里的实际文件** |
| 结果 | `WVPAGE_OK\n\nhello from probe`（**渲染后的 innerText**） | **`<html><body><h1...>WVPAGE_OK...</h1>...`（原始 HTML 源码）** |
| 含义 | **HTML 被正常解析** | **HTML 被当纯文本显示** |

**所以**：
> **`file://` 在容器里能读到文件内容（否则 body 会是 EMPTY 或报错）**
> **但内容被当成 `text/plain` 显示，而不是按 `text/html` 解析**
>
> **这与 Via 主页显示源码的现象完全一致。**

**这直接推翻了之前所有关于"renderer 读不到/平台限制"的猜测**（那些猜测已在
`更正-WebView读取路径不是平台限制.md` 里作废）。

---

## 一、完整实验结果

### W0：WebView 能否创建 —— **FAIL，但原因是我测试代码的错**

```
W0 FAIL: new WebView() threw RuntimeException:
  java.lang.Throwable: A WebView method was called on thread 'wvprobe'.
  All WebView methods must be called on the same thread.
  (Expected Looper Looper (main, tid 2) {76aae27} called on null,
   FYI main Looper is Looper (main, tid 2) {76aae27})
```

**我在后台线程 `new WebView(...)`** —— WebView 必须在有 Looper 的线程创建。
**这是我的测试代码 bug，不是容器问题。**

**修正方案**：把 `createWebView()` 完全放进 `runOnUiThread`，用 `CountDownLatch` 等结果
（代码里已经这么写了，但 `new WebView(this)` 那行仍在后台线程 —— 因为整个 `runAll` 在后台线程跑）。

### W5：`ApplicationInfo.getAllApkPaths()` —— **PASS，路径全对**

```
W5 ok: getAllApkPaths() n=10
W5   apkPath: /data/user/0/com.nextvm.app/files/virtual/apks/com.sandbox.probe_1795516976639.apk  exists=true
W5   apkPath: /product/app/WebViewGoogle64/WebViewGoogle64.apk                                exists=true
W5   apkPath: /product/app/TrichromeLibrary64/TrichromeLibrary64.apk                          exists=true
W5   apkPath: /system_ext/framework/androidx.window.extensions.jar                            exists=true
W5   apkPath: /data/resource-cache/com.android.systemui-neutral-SAbe.frro                     exists=true
W5   apkPath: /data/resource-cache/com.android.systemui-accent-LZ5k.frro                      exists=true
W5   apkPath: /data/resource-cache/com.android.systemui-dynamic-M0Kl.frro                     exists=true
（后 3 个重复了一次）
```

**关键**：
- **`getAllApkPaths()` 在容器里返回的是"guest APK + WebView 的两个 APK + system 的 jar/frro"**
- **`WebViewGoogle64.apk` 和 `TrichromeLibrary64.apk` 都在，且 `exists=true`**
- **`/data/resource-cache/...frro` 是 vivo 的 overlay 资源，也带上了**

**所以 AOSP `WebViewFactory` 用来加 shared library 的路径清单在容器里是正确的** ✓
→ **排除了 V2 假设（路径不对）**

### W6：宿主 AssetManager 里有没有 WebView 资源 —— **部分命中，判据设计有误**

```
W6 info: getAssets() class=android.content.res.AssetManager hashCode=135527795
W6 list(webkit/) -> 1 entries
W6 list(webview_assets.json) -> 0 entries
W6 list(ic_webkit_) -> 0 entries
W6 MISS: assets.open(webview_assets.json) 失败（FileNotFoundException）
```

**关键**：
- **`list("webkit/") -> 1 entries`** → **宿主 AssetManager 里确实有 `webkit/` 目录**！
  说明 **`addAssetPathAsSharedLibrary` 已经生效**（否则连 `webkit/` 都不会有）
- **但 `webview_assets.json` 找不到** → **这个文件名在 vivo 的 WebView 里不存在**
  （我猜的判据，不通用）

**结论**：
- **V3/V4 假设（`getAssets()` 返回错的东西 / `addAssetPathAsSharedLibrary` 没生效）大概率不成立**
- **需要换一个更可靠的判据**：比如 `list("webkit/")` 的 1 个 entry 是什么？

**待办**：探针里打印 `am.list("webkit/")` 的实际内容。

### W7：`0x02` 域资源能否解析 —— **FAIL**

```
W7 FAIL: getInteger(0x020c0006) -> NotFoundException: Resource ID #0x20c0006
W7 probe: getValue(0x020c0006) -> NotFoundException
W7 FAIL: getInteger(0x020c0000) -> NotFoundException
W7 FAIL: getInteger(0x020c0001) -> NotFoundException
```

**但这不能直接推出 popup 崩溃** —— 因为我查的是**探针自己的 `Resources`**，
而 WebView 的 `onCreateActionMode` 用的是 **WebView 自己的 `Resources`**（从 WebView context 拿）。

**要验证 popup 崩溃，必须用真实的 WebView 触发 `onCreateActionMode`**（长按文字）。
**探针 W7 只能说明"guest 的 Resources 里没有 0x02 域资源"，这本来就是预期的。**

### W1：`loadDataWithBaseURL` —— **PASS**

```
W1 url=about:blank -> finished body="WVPAGE_OK\n\nhello from probe"
```

**HTML 被正常解析成 DOM，`innerText` 是渲染后的文本。**
→ **renderer 完全正常，WebView 渲染链路没问题** ✓

### W2：`file://` + 沙箱路径 —— **能读，但按纯文本显示（核心发现）**

```
W2 url=file:///data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795516976639/files/wvprobe.html
   -> finished body="\u003Chtml>\u003Cbody>\u003Ch1 id='h'>WVPAGE_OK\u003C/h1>\u003Cp>hello from probe\u003C/p>\u003C/body>\u003C/html>"
```

**注意三点**：
1. **`onPageFinished` 触发了** → 没有报错
2. **`body` 是完整的 HTML 源码** → 文件被读到了
3. **但显示成源码** → **MIME 被判定成 `text/plain`，不是 `text/html`**

**这与 Via 主页的现象完全一致。**

### W3：`file:///android_asset/` —— **FAIL**

```
W3 url=file:///android_asset/wvprobe.html -> error code=-1 desc=net::ERR_FILE_NOT_FOUND
```

**原因**：探针的 assets 里没有 `wvprobe.html`。
我确实创建了 `probe/src/main/assets/wvprobe.html`，但 **CI 构建时可能没打包进去**
（或者我创建的文件路径不对）。

**待查**：`unzip -l probe-bddb6aa-arm64-v8a.apk | grep assets`

### W4：`file://` + `getDataDir()/files` —— **FAIL（测试代码 bug）**

```
PREP copy -> /data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795516976639/files/wvprobe.html len=0
W4 url=file:///data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795516976639/files/wvprobe.html
   -> finished body="EMPTY"
```

**原因**：`copy()` 复制后长度是 0 —— 因为源文件和目标文件是**同一个路径**！
`getFilesDir()` 和 `getDataDir()/files` 在容器里**指向同一个沙箱路径**，所以 copy 自己覆盖自己
（`new FileOutputStream(to, false)` 先把文件截断了）。

**这是我的测试代码 bug，不是容器问题。**

---

## 二、结论汇总

| 假设 | 结果 | 证据 |
| --- | --- | --- |
| **V1** `createApplicationContext` 返回错的东西 | ❓ 未测 | — |
| **V2** `getAllApkPaths()` 路径不存在 | ❌ **排除** | W5 全部 `exists=true` |
| **V3** `getAssets()` 返回 guest 的 AssetManager | ⚠️ **大概率排除** | W6 `list("webkit/") -> 1 entry` |
| **V4** `addAssetPathAsSharedLibrary` 没生效 | ⚠️ **大概率排除** | 同上（`webkit/` 存在） |
| **V5** `IWebViewUpdateService` 被干扰 | ❓ 未测 | WebView 能创建（W1 成功），所以服务通了 |
| **新 C4：file:// 的 MIME 判定** | ✅ **成立** | **W2 读到文件但按纯文本显示** |

**所以 Via 主页问题的真因是 C4：`file://` 加载的 HTML 被当成纯文本。**

**这不是"读不到"，是"MIME 判定错"。**

---

## 三、下一步

### 3.1 修探针的三个 bug（我的错）

| # | bug | 修法 |
| --- | --- | --- |
| **1** | W0 在后台线程 `new WebView()` | 把整个 `runAll` 逻辑放进 `runOnUiThread`，或者 WebView 相关操作全部 `runOnUiThread` |
| **2** | W4 copy 自己覆盖自己 | 用不同的目标文件名（如 `wvprobe_w4.html`） |
| **3** | W3 assets 没打包 | 查 APK 里有没有 `assets/wvprobe.html`，确认构建配置 |

### 3.2 新增实验（针对 MIME 判定）

| # | 实验 | 目的 |
| --- | --- | --- |
| **W8** | `loadUrl("file://...html")` 后读 `webView.getUrl()` + `WebResourceResponse` 的 `mimeType` | 看实际 MIME 是什么 |
| **W9** | `shouldInterceptRequest` 里打印 `request.getUrl()` + 自己返回 `text/html` | 测"强制指定 MIME 能否修复" |
| **W10** | 用 `file://.../x.txt`（内容是 HTML）加载 | 对照：扩展名不是 .html 时是否也显示源码 |
| **W11** | `am.list("webkit/")` 的实际内容 | 确认 WebView 资源到底加进去了什么 |

### 3.3 如果确认是 MIME 判定

**可能的容器原因**：
1. **native hook 影响了 `libc` 的 `stat`** → WebView 拿不到正确的文件大小/类型
2. **`MimeTypeMap` 的 `shared_lib` 表被破坏**
3. **`guessContentTypeFromName` 走了 native，被 hook 影响**

**验证**：在探针里直接调 `MimeTypeMap.getSingleton().getMimeTypeFromExtension("html")` 和
`URLConnection.guessContentTypeFromName("x.html")`，看返回值。

---

## 四、时间与设备约定

**本次起，所有时间戳以手机时间为准**：

- 设备时间：`11-24 18:42:16`
- 电脑时间：`11-24 18:40:25`
- **设备比电脑快约 1 分 51 秒**

**logcat 的时间戳是设备时间**，与电脑时间对比时要减 1 分 51 秒。
**所有 `-T` 过滤必须用 `adb shell date` 取设备时间**（已按此执行）。
