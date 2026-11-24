# 修改计划：WebView `file://` MIME 修复（方案 A —— `shouldInterceptRequest` 补 MIME）

- 日期：2026-11-24
- 依据：`docs/records/记录-WebView根因定位-W14沙箱路径.md`（本轮实测）
- 状态：**计划待确认，尚未改任何容器代码**

---

## 一、目标

一句话：让容器内 guest 用 `file://` 加载**自己的 HTML** 时，WebView 收到的 MIME 是 `text/html` 而不是 `text/plain`，
从而 Via 主页（`getFilesDir()/homepage2.html`）正常渲染。

---

## 二、为什么只能在这层修（已排掉的路）

实测结论（详见记录文档第四之二、四之三节）：

| 事实 | 证据 |
| --- | --- |
| 沙箱前缀的 `file://` MIME **来自内容嗅探** | 同一 `.html` 文件内容改成 `GIF89a` 开头 → `contentType=image/gif` |
| `/data/local/tmp` 前缀走**扩展名**判定 | 同一文件、同一 WebView，`contentType=text/html` |
| 前缀是决定因素，与真实 inode 无关 | `/data/local/tmp` 下**符号链接指向沙箱文件** → 仍 `text/html` |
| 写入者不是变量 | 文件由 adb 放入沙箱 → 仍 `text/plain` |
| 不是 `virtual/data` 路径段 | 容器私有目录**根**下的 `.html` → 仍 `text/plain` |
| native hook 不参与 MIME | `NS_HOOK_SYSTEM_LIBS=0`；`redirect_path()` 只是 `open/stat` 的入参前缀替换 |
| 容器 Java 层没有 MIME 相关 hook | 全仓 `grep` `MimeTypeMap`/`AndroidProtocolHandler`/`shouldInterceptRequest` = 0 命中 |

**推断出的机制**：沙箱前缀的 file 请求落到 Java `AndroidProtocolHandler.getMimeType(stream, url)` 的兜底分支
（`URLConnection.guessContentTypeFromStream`），它只认 Java 序列化/GIF/JPEG/PNG 魔数，HTML 没魔数 → null → 上层默认 `text/plain`。
源码与行为逐行吻合。

**→ 「改 `VirtualContext` 路径映射就能修好」这条乐观分支不成立**（MIME 不看路径，看内容嗅探）。
**→ 唯一已验证有效的修复点是 `shouldInterceptRequest`（W11）。**

### 明确不做

| 方案 | 为什么不做 |
| --- | --- |
| 把 guest `getFilesDir()` 改成 `/data/local/tmp/...` + redirect 规则 | `hooked_opendir()` 没有重定向逻辑（`native-hook.cpp:1289` 只打印后直通），改成外部前缀会让**目录枚举**失效；且所有 guest 的路径检测面全变 |
| 给 WebView 的 AssetManager 加资源（`ensureWebViewResources`） | 已实测导致 Via boot loop（`2fc764d` 回退） |
| hook `URLConnection.guessContentTypeFromStream`（Java 静态方法） | 容器没有方法体替换能力（无 Xposed，ART inline hook 框架未覆盖 Java 方法） |
| 改 `libwebviewchromium` | `NS_HOOK_SYSTEM_LIBS=0` 的既有结论，影响全局 |

---

## 三、方案 A 的最小验证（先做这一步，不动容器）

> **原则：先在探针里证明"包装 client 能修好"，再动容器。**

探针 v1.13 新增 **W30**：完全模拟容器将来要做的事——

```java
// 包装成"尊重应用 client + 对 file:// 补 MIME"的 client，然后加载 W2 的同一个 URL
WebViewClient original = new WebViewClient();            // 模拟 Via 的 client（shouldInterceptRequest 返回 null）
wv.setWebViewClient(new MimeFixClient(original));        // 容器版的等价物
wv.loadUrl("file://" + ff1.getAbsolutePath());
```

判据：

| W30 结果 | 含义 |
| --- | --- |
| `ct=text/html` + 渲染出 `WVPAGE_OK` | 包装路径成立 → 可以动容器 |
| 仍 `text/plain` | 说明嗅探发生在 Java 分支更深处，方案 A 也要重新设计 |

改动范围：`probe/src/main/java/com/sandbox/probe/WebViewProbeActivity.java`（1 文件，约 +70 行）。

---

## 四、方案 A 在容器里的实现（W30 通过后才做）

### 4.1 影响范围

| 文件 | 改动类型 | 说明 |
| --- | --- | --- |
| `core/virtualization/src/main/java/com/nextvm/core/virtualization/webview/WebViewMimeFix.kt` | **新增** | 视图树扫描 + client 包装 + `file://` 补 MIME |
| `core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/VirtualApplicationManager.kt` | 修改 | 在 `onActivityResumed`（v131 已有的遍历点）和 v124 定时器里调用 `WebViewMimeFix.scan(a)`；加开关 |
| `core/sandbox/.../VirtualConstants.kt`（若需要） | 修改 | 加 `webviewMimeFixEnabled()` 开关（默认 **开**，可在 CmdReceiver 关掉） |

**不改**：native hook、WebView 构造、AssetManager、Via 任何代码。

### 4.2 核心逻辑（预期代码，非最终 diff）

```kotlin
// WebViewMimeFix.kt
object WebViewMimeFix {
    private val wrapped = java.util.Collections.newSetFromMap(
        java.util.WeakHashMap<android.webkit.WebView, Boolean>())

    fun scan(root: android.view.View) {
        if (!VirtualConstants.webviewMimeFixEnabled()) return
        // 深度遍历找 android.webkit.WebView
        // 每个 WebView 只处理一次（WeakHashMap 记账）
    }

    private fun install(wv: android.webkit.WebView) {
        // 1) 反射取现有 client：先试 mWebViewClient，失败则在 declaredFields 里找 WebViewClient 类型
        val field = android.webkit.WebView::class.java.declaredFields
            .firstOrNull { android.webkit.WebViewClient::class.java.isAssignableFrom(it.type) }
            ?: return
        field.isAccessible = true
        val original = field.get(wv) as? android.webkit.WebViewClient
        // 2) 包装（original 可为 null —— 应用还没设 client）
        field.set(wv, MimeFixClient(original))
    }
}

class MimeFixClient(private val orig: android.webkit.WebViewClient?) : android.webkit.WebViewClient() {
    override fun shouldInterceptRequest(
        view: android.webkit.WebView, request: android.webkit.WebResourceRequest
    ): android.webkit.WebResourceResponse? {
        // 1) 尊重应用的 client（Via 处理 homepage.css 走这里）
        orig?.shouldInterceptRequest(view, request)?.let { return it }
        // 2) 只接管 file://
        val u = request.url ?: return null
        if (u.scheme != "file") return null
        return openFileWithMime(view, u)   // 见下
    }

    // 3) 其余应用可能覆写的方法要转发（避免丢行为）
    override fun onPageFinished(v: android.webkit.WebView, url: String?) { orig?.onPageFinished(v, url) }
    override fun onPageStarted(v: android.webkit.WebView, url: String?, f: android.graphics.Bitmap?) {
        orig?.onPageStarted(v, url, f)
    }
    override fun shouldOverrideUrlLoading(v: android.webkit.WebView, r: android.webkit.WebResourceRequest?): Boolean =
        orig?.shouldOverrideUrlLoading(v, r) ?: false
    override fun onReceivedError(v: android.webkit.WebView, r: android.webkit.WebResourceRequest?, e: android.webkit.WebResourceError?) {
        orig?.onReceivedError(v, r, e)
    }
    override fun onLoadResource(v: android.webkit.WebView, url: String?) { orig?.onLoadResource(v, url) }
    override fun doUpdateVisitedHistory(v: android.webkit.WebView, url: String?, reload: Boolean) {
        orig?.doUpdateVisitedHistory(v, url, reload)
    }
}

private fun openFileWithMime(view: android.webkit.WebView, u: android.net.Uri): android.webkit.WebResourceResponse? {
    return try {
        val path = u.path ?: return null
        if (path.startsWith("/android_asset/")) {                       // 顺带修 W3
            val rel = path.removePrefix("/android_asset/")
            val mime = mimeFor(rel) ?: "application/octet-stream"
            return android.webkit.WebResourceResponse(mime, "utf-8",
                view.context.assets.open(rel, android.content.res.AssetManager.ACCESS_STREAMING))
        }
        val f = java.io.File(path)
        if (!f.isFile) return null                                     // 读不到就让 WebView 自己报错
        android.webkit.WebResourceResponse(mimeFor(path) ?: "application/octet-stream", "utf-8",
            java.io.FileInputStream(f))
    } catch (_: Throwable) { null }
}

private fun mimeFor(path: String): String? =
    android.webkit.MimeTypeMap.getSingleton()
        .getMimeTypeFromExtension(android.webkit.MimeTypeMap.getFileExtensionFromUrl(path.lowercase()))
    // 兜底：HTML 家族明确给 text/html（Via 的 homepage2.html 走这条）
```

### 4.3 接线

```kotlin
// VirtualApplicationManager.registerBreadcrumbCallbacks 里
override fun onActivityResumed(a: android.app.Activity) {
    ...
    disableAutofillPerView(a)                 // 已有（v131）
    WebViewMimeFix.scan(a.window?.decorView)  // 新增
}
// v124 定时器里同样加一次（覆盖 onResume 之后才 inflate 的 WebView）
```

### 4.4 与 Via 的兼容性

Via 的 `d9.q$c.shouldInterceptRequest` 只对 `homepage.css` 返回非 null，其余 return null
（`docs/records/记录-反编译Via主页加载全流程.md`）→ **容器只在"应用返回 null 且 scheme=file"时接管**，零冲突。

副作用（可接受）：Via 自己补 `text/css` 的那条仍由 Via 返回，容器不参与。

---

## 五、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | **包装 client 丢失应用覆写的方法**（主要回归点） | 4.2 里显式转发高频方法；W30 验证；Via 实测看主页/长按/链接 |
| 2 | 反射字段名在 Android 15 变化 | 不硬编码 `mWebViewClient`，按**类型**扫 `declaredFields` |
| 3 | 应用在 onResume 之后又换 client → 逃逸 | 已知限制，计时器兜底；Via 没这个行为 |
| 4 | `file://` 大类文件（大视频）走 Java 流 → 内存/性能 | 只接管 `mimeFor(path)` 能识别的扩展名，且 `WebResourceResponse` 用流式 InputStream；无法识别时直接 return null（不改现有行为） |
| 5 | asset 分支可能让 W3 从"报错"变成"能打开"，但也可能引入新的资源路径问题 | asset 只在 `path.startsWith("/android_asset/")` 时接管；失败回落 null |
| 6 | 影响所有 guest（不只 Via） | 开关默认开，CmdReceiver 可关；出问题时 revert |

**回退方案**：

```powershell
git -C _ref/ShitJumpingVM revert <webview-mime-fix-sha>     # 整笔回退
# 或只关功能（不需要重新构建）：
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.SETTINGS --es k webviewMimeFix --es v 0
```

**验证方法**：

```powershell
# 1) 探针：W2/W4/W15/W16 应为 ct=text/html 或正常渲染；W14 不退化
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH_ACTIVITY `
    --es pkg com.sandbox.probe --es activity com.sandbox.probe.WebViewProbeActivity
adb shell run-as com.nextvm.app cat files/virtual/data/<inst>/files/wvprobe-result.txt

# 2) Via：主页应显示页面而非源码
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path /data/local/tmp/via.apk
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg mark.via
adb exec-out screencap -p > docs/records/img/via-home-after.png
adb shell pidof mark.via     # 60 秒后仍在 = 无 boot loop
```

---

## 六、执行顺序

| 顺 | 动作 | 依赖 | 谁确认 |
| --- | --- | --- | --- |
| 1 | 探针 v1.13 加 W30（不动容器），跑出 `ct=text/html` | — | 本计划 |
| 2 | 容器实现 `WebViewMimeFix` + 接线 | 1 通过 | **需用户确认后再改容器** |
| 3 | dispatch `container-build.yml`（**只一次**） | 2 | — |
| 4 | 装容器、导入探针+Via、跑验收 | 3 | — |
| 5 | 追加 `## 实际变更` + `.actual.diff` | 4 | — |

> 第 1 步不改容器，风险为零；第 2 步动的是「hook / 生命周期接线」，属于 AGENTS.md 要求先落盘计划的范围 —— **本文即该计划，落盘后停下等确认。**
