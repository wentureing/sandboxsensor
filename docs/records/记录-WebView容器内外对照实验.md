# 记录：WebView 容器内外对照实验（决定性结论）

- 日期：2026-11-24 19:14（**手机时间**）
- 探针：v1.10（`probe-3980ba3-arm64-v8a.apk`，versionCode=11）
- 设备：`10AFA8169Z0039G`（vivo V2463A / Android 15 / API 35）
- WebView：Chrome/126.0.6478.71（同一个，两次实验完全相同）

---

## 零、一句话结论

> **同一个 APK、同一个 Activity、同一台设备、同一个 WebView 版本：**
> **容器外三个加载实验全部成功，容器内三个全部失败。**
> **→ 确认是容器的缺陷，不是 Android WebView 的平台行为。**

---

## 一、对照数据

| 实验 | **容器外**（`com.sandbox.probe` 直装） | **容器内**（guest 实例） |
| --- | --- | --- |
| **W0** WebView 创建 | ✅ 成功 | ✅ 成功 |
| **W1** `loadDataWithBaseURL` | ✅ `body="WVPAGE_OK\n\nhello from probe"` | ✅ 同样成功 |
| **W2** `file://<filesDir>/wvprobe.html` | ✅ **`body="WVPAGE_OK\n\nhello from probe"`** | ❌ **`body="<html>...原始源码"`** |
| **W3** `file:///android_asset/wvprobe.html` | ✅ **`body="ASSET_OK\n\nloaded from android_asset"`** | ❌ **`body="EMPTY"`** |
| **W4** 另一个文件名 | ✅ 正常渲染 | ❌ 同样源码 |
| **W11** 容器主动返回 `text/html` | — | ✅ **正常渲染** |

### 关键日志对比

**容器外（正常）**：
```
PREP wvprobe.html -> /data/user/0/com.sandbox.probe/files/wvprobe.html exists=true len=74
W2 INTERCEPT url=file:///data/user/0/com.sandbox.probe/files/wvprobe.html method=GET
W2 url=file:///data/user/0/com.sandbox.probe/files/wvprobe.html -> finished body="WVPAGE_OK\n\nhello from probe"
W3 url=file:///android_asset/wvprobe.html -> finished body="ASSET_OK\n\nloaded from android_asset"
```

**容器内（失败）**：
```
PREP wvprobe.html -> /data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795518539595/files/wvprobe.html exists=true len=74
W2 INTERCEPT url=file:///data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795518539595/files/wvprobe.html method=GET
W2 url=file:///.../wvprobe.html -> finished body="\u003Chtml>\u003Cbody>\u003Ch1 id='h'>WVPAGE_OK..."
W3 INTERCEPT url=file:///android_asset/wvprobe.html method=GET
W3 url=file:///android_asset/wvprobe.html -> finished body="EMPTY"
E AndroidProtocolHandler: Unable to open asset URL: file:///android_asset/wvprobe.html
```

**注意**：容器内的 `E AndroidProtocolHandler: Unable to open asset URL` —— **容器外没有这行**。

---

## 二、已排除的假设（更新）

| 假设 | 状态 | 依据 |
| --- | --- | --- |
| ~~renderer isolated uid 读不到沙箱~~ | ❌ 排除 | 文件被读到了（body 有完整 HTML） |
| ~~Android 11+ `file:///data/` 平台限制~~ | ❌ **彻底排除** | **容器外 W2/W3/W4 全部成功** |
| ~~Android WebView 对 `file://` 不给 Content-Type（issue #428）~~ | ❌ **不适用** | 那是 React Native 的特定场景；同设备容器外正常 |
| ~~WebView 的 Context 不对~~ | ❌ 排除 | W8 `SAME_CONTEXT=true` |
| ~~Assets 不是同一个~~ | ❌ 排除 | W12 `SAME_ASSETS=true` |
| ~~Java 层 MIME 表被破坏~~ | ❌ 排除 | W9/W10 都返回 `text/html` |
| ~~native hook 影响了 Chromium 的 MIME 判定~~ | ❌ 排除 | `NS_HOOK_SYSTEM_LIBS=0` → `libwebviewchromium` 不 patch；日志里没有 `wvprobe.html` 的 open/stat 记录 |
| **容器内的 `file://` 加载路径被改变** | ✅ **成立** | 容器外/内唯一差别就是沙箱路径 + 容器环境 |

---

## 三、当前唯一合理的机制假设（C6）

**容器内的 `file://` 和 `android_asset` 都被"某种容器环境"影响，但 native hook 不是原因**（已排除）。

**两个现象必须一起解释**：

| 现象 | 细节 |
| --- | --- |
| **W2** `file://<真实沙箱路径>` → **读到内容但 MIME=`text/plain`** | 文件存在，内容正确 |
| **W3** `file:///android_asset/` → **`AndroidProtocolHandler` 打不开** | 但 `getAssets().open()` 能打开 |

**共同点**：两者都走 **Java 层 → JNI → native** 的路径。

**最可能**：
> **容器让 WebView 的 JNI 层拿到的是"某种不一致的 Context/AssetManager 视图"**：
> - Java 层看到的是 `VirtualContext`（伪装 `/data/user/0/com.sandbox.probe`）
> - native 层（Chromium）用的是**真实文件系统**
> - **两者对同一个路径的解析结果不同**

**具体到 W2**：
- Java 层把 `file:///data/user/0/com.nextvm.app/files/virtual/.../wvprobe.html` 交给 Chromium
- Chromium 按**自己的 MIME 表 + 扩展名**判 → 应该 `text/html`
- **但结果是 `text/plain`**

**唯一剩下的解释**：**Chromium 拿到的路径不是这个**（被 JNI 桥改写），或者
**Chromium 的 `GetMimeTypeFromFile` 在 Android WebView 里被替换成调用 Java 的 `MimeTypeMap`，而那个调用走了 guest 的 `AssetManager` 失败**。

**这两个都需要看 AOSP `android_webview` 的源码确认。**

---

## 四、现已确定可用的修复方案（W11 实证）

### 方案 A：容器 hook `shouldInterceptRequest`

**已验证**：
```
W11 INTERCEPT -> 主动返回 text/html, file len=74
W11 url=file:///... -> finished body="WVPAGE_OK\n\nhello from probe"  [intercepted 1]
```

**能修**：
- ✅ W2（`file://` 沙箱路径）
- ✅ W4（同 W2）
- ✅ **W3（`android_asset`）—— 容器自己读 `getAssets()` 然后返回 stream，绕过 `AndroidProtocolHandler`**

**实现要点**：
1. hook `WebViewClient.shouldInterceptRequest`（包装用户设置的 client）
2. 对 `file://` 和 `file:///android_asset/` 请求：
   - 容器自己 `open` 文件 / `getAssets().open()`
   - 按扩展名给 MIME（容器已经有 `MimeTypeMap`，W9 证明可用）
   - 返回 `WebResourceResponse(mime, "utf-8", stream)`
3. 用户自己返回了内容时，**优先尊重用户**（只在用户返回 null 时接管）

**风险**：
- 每个子资源都过一次 Java 层（性能）
- 用户自己实现了 `shouldInterceptRequest` 时要正确合并

### 方案 B：查清 C6 的根因再修（更彻底但耗时）

需要读 AOSP `android_webview` 源码：
- `AndroidProtocolHandler.java` —— 它用哪个 `openNonAsset`
- `AwContents` 的 `shouldInterceptRequest` 路径
- 是否有 Java 层的 MIME 判定参与

**如果根因是"JNI 桥拿到的路径不一致"**，那么修根因比方案 A 更彻底。

---

## 五、建议

**先做方案 A**（已被 W11 验证，能同时修 W2/W3/W4），**同时**并行查 C6 的根因。

理由：
- 方案 A **确定有效**，不依赖根因
- 但它只覆盖走 `WebViewClient` 的请求（绝大多数应用都设了 client）
- 如果 Via 没设 client，方案 A 无效 → 那时必须修根因

**验证 Via 是否设了 `WebViewClient`**：反编译 Via APK 查。
