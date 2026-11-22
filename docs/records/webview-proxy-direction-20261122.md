# 三件事分析：复刻 WebView 进程 / Android 12 权限差异 / Gaia 为何正常

- 日期：2026-11-22 21:30
- 背景：p17（WebView hook）已回退，分支 `backup-webview-hook` 保存
- 用户新观察：**Android 12 手机上 WebView 访问 `/sdcard/Download` 报 denied，但应用隐藏大师（Gaia）能正常加载 webview 和 via**

---

## 一、能否在容器内自己克隆进程、复刻 WebView 行为

### 结论：**不可行**（应用层没有这个能力）

WebView 的渲染进程启动链路是**系统控制**的：

```
new WebView(context)
  → WebViewFactory.getProvider()
    → WebViewChromiumFactoryProvider
      → 首次渲染时，由系统的 webview_zygote 进程 fork 出 renderer
        → renderer 是 isolated uid，与宿主完全隔离
```

**应用能碰到的边界**：

| 能做的 | 不能做的 |
| --- | --- |
| `WebView.setDataDirectorySuffix()` 改数据目录 | 选择/创建 renderer 进程 |
| `WebSettings` 各种开关 | 传 Chromium 命令行 flag（如 `--single-process`） |
| `WebViewClient.shouldInterceptRequest` 拦截请求 | 让 WebView 在容器自己的进程里渲染 |

`--single-process` 确实是 Chromium 支持的 flag，但 Android 的 `WebViewFactory` 不暴露入口，**应用层无法传**。

**唯一"换进程模型"的办法是换内核**：

| 内核 | 能否单进程 | 代价 |
| --- | --- | --- |
| 系统 WebView（Chromium） | ❌ | — |
| 腾讯 X5 / TBS | ⚠️ 可配 | 引入第三方 SDK（几十 MB）、维护成本、X5 自身在收缩 |
| 自研（如 GeckoView） | ⚠️ | 体量更大，且不是 WebView API 兼容 |

**所以这条路不建议走。**

---

## 二、Android 12 上 WebView 访问 `/sdcard/Download` 被 denied

### 这是**系统行为**，不是容器造成的

| 层 | 说明 |
| --- | --- |
| `file://` 访问开关 | `WebSettings.setAllowFileAccess()` 在 API 30+ 默认 `false`，但应用可以打开 |
| **沙箱进程权限** | WebView renderer 是 **isolated uid**，**无法继承应用的存储权限** |
| Android 版本差异 | Android 12 上 isolated 进程访问 `/sdcard` 被 SELinux/appop 拒绝；Android 15 上表现更宽松（也可能是 WebView 版本差异：用户设备是 126.0.6478.71） |

**关键**：`denied` 来自**内核/SELinux**，因为 isolated uid 没有 `sdcard_rw` 之类的权限。

**这解释了为什么"WebView 直连 `/sdcard/Download` 在 Android 12 上失败"** —— 和容器无关，纯系统。

---

## 三、为什么 Gaia（应用隐藏大师）能正常 —— **这是容器技术差异**

### 3.1 如果是纯系统限制，Gaia 也应该失败

但用户实测 **Gaia 能正常加载 webview 和 via**，说明 Gaia **绕过了 `file://`**。

### 3.2 最可能的机制：`WebResourceResponse` 代理

WebView 有一个关键能力：**宿主可以拦截请求并返回自己的数据流**。

```java
webView.setWebViewClient(new WebViewClient() {
    @Override
    public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest req) {
        if (req.getUrl().toString().startsWith("file://")) {
            // 宿主自己打开文件（有权限），把 stream 交给 WebView
            InputStream in = new FileInputStream(realPath);
            return new WebResourceResponse("text/html", "utf-8", in);
        }
        return null;
    }
});
```

**为什么这个能绕过限制**：

| 通道 | 谁打开文件 | 是否受 isolated uid 限制 |
| --- | --- | --- |
| `file://` 直接访问 | **renderer 进程自己** | ✅ 受限（denied） |
| `WebResourceResponse` | **宿主进程**（有存储权限） | ❌ 不受限（流/FD 跨进程传递） |

**这正是我们之前那个"UI 能导入、命令行不能"的同一个原理**：**fd/stream 绕过路径解析**。

### 3.3 但 Gaia 怎么把 `shouldInterceptRequest` 注入进 guest？

这是关键问题。可能的实现：

| 方式 | 说明 |
| --- | --- |
| **A. 替换 guest 的 `WebViewClient`** | 在 guest 创建 WebView 时 hook 掉默认 client，套一层自己的拦截器 |
| **B. 代理 `WebView` 类本身** | 用 `ClassLoader` 加载一个改过的 `WebView`（复杂） |
| **C. 只处理自己可控的页面** | 比如 Via 的本地页由 Gaia 预先改写（但 Via 是第三方，不现实） |

**A 最可能**：Gaia 在 guest 进程里 hook `WebView.setWebViewClient`，给每个 client 包一层"文件协议拦截"。

### 3.4 对我们的启示：**这条路我们也能走**

我们容器已经有 `BinderProxyManager` / `ActivityThreadHook` 这类 Java 层 hook 基础设施。加一个：

```
hook WebView.setWebViewClient（或在 ActivityThreadHook 里给每个 guest Activity 的 WebView 包一层）
  → 拦截 file:// 请求
    → 宿主用真实路径（容器翻译后）打开文件
      → 返回 WebResourceResponse(正确的 MIME, stream)
```

**收益**：
- 解决 Via 主页源码（MIME 正确）
- 解决 WebView 读容器沙箱文件（宿主代读）
- 不受 WebView 多进程/isolated uid 限制

**代价**：
- 需要 hook `WebView` 的 Java API（比 native hook 简单，但覆盖面要够）
- 需要处理 MIME 推断（按扩展名）

---

## 四、三件事的结论汇总

| # | 问题 | 结论 |
| --- | --- | --- |
| 1 | 容器内自己克隆进程复刻 WebView | **不可行**。渲染进程由 `webview_zygote` fork，应用层无入口；换内核代价大 |
| 2 | Android 12 `file:///sdcard` denied | **系统行为**（isolated uid 无存储权限），与容器无关 |
| 3 | Gaia 为何正常 | **容器技术差异**：几乎可以确定它用 `WebResourceResponse` 把 `file://` 请求改为"宿主代读 + 流传递"，绕过 isolated uid 限制 |

## 五、建议的下一步

**如果要把 Via 这类应用做通**，方向是：

```
在 guest 里 hook WebView 的请求拦截点
  → file:// 请求由宿主代读（走容器路径翻译）
    → 用 WebResourceResponse 返回正确的 MIME
```

这比"patch WebView native 库"有效得多，因为它在**正确的层**（宿主进程能控制的层）解决问题。

**验证方法**：

1. 先确认 Gaia 确实用了这个机制（反编译找 `shouldInterceptRequest`，或抓 Gaia 容器里 Via 的日志）
2. 再在我们的容器里实现同样机制
3. 用探针的 `WebViewTestActivity` 做回归（加载本地 HTML + 长按 popup）
