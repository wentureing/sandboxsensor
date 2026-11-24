# 计划：WebView 故障修复（唯一权威版）

- 建立：2026-11-24 18:50（**手机时间**；设备比电脑快约 1 分 51 秒）
- 容器基线：`1.0.0-c00f1cb`（含 `2fc764d` 回退，Via 已能正常启动）
- 本文件**取代**以下已作废文档的结论（这些文件已删除）：
  - `调研/WebView问题与缺陷映射.md`（D1/D2「平台限制」已被推翻）
  - `调研/更正-WebView读取路径不是平台限制.md`（更正正确，但六种猜测已被实测收敛）
  - `计划-修正与受控测试方案.md`（测试已执行完，被 W0–W7 结果取代）
  - `计划-四问回答与下一步顺序.md`（核心结论「renderer 读真实路径」被推翻）

---

## 一、已确认的事实（W0–W7 实测，不再讨论）

来源：`docs/records/记录-WebView受控实验W0W7结果.md`

| # | 事实 | 证据 |
| --- | --- | --- |
| **F1** | **WebView 渲染链路正常** | W1 `loadDataWithBaseURL` → `body="WVPAGE_OK hello from probe"`（渲染后的 innerText） |
| **F2** | **`file://` 能读到文件，但被当纯文本显示** | W2 `body="<html><body><h1...>WVPAGE_OK..."`（原始 HTML 源码） |
| **F3** | **`getAllApkPaths()` 路径全对** | W5 n=10，`WebViewGoogle64.apk` + `TrichromeLibrary64.apk` 都 `exists=true` |
| **F4** | **shared library 大概率已加载** | W6 `list("webkit/") -> 1 entry` |
| **F5** | **`file:///android_asset/` 在容器里读不到** | W3 `ERR_FILE_NOT_FOUND`，但 APK 里确实有 `assets/wvprobe.html` |
| **F6** | **Via 主页与 W2 现象一致** | 都是"HTML 被当文本显示" |

### 已被实测排除的假设

| 假设 | 排除依据 |
| --- | --- |
| ~~renderer isolated uid 读不到沙箱~~ | **F2：文件读到了** |
| ~~Android 11+ `file:///data/` 平台限制~~ | **F2：容器内读到了；Via 标准环境也正常** |
| ~~`getAllApkPaths()` 路径不对~~ | **F3** |
| ~~`addAssetPathAsSharedLibrary` 没生效~~ | **F4** |
| ~~Via 用 `TextView` 而不是 WebView~~ | **W2 用真实 WebView 复现了同样现象** |
| ~~容器 hook 让 renderer 崩溃~~ | **F1：渲染正常** |

---

## 二、当前唯一假设：**C4 — WebView 拿到的 Context 不对**

### 2.1 为什么这么猜

F2 和 F5 **可能是同一个根因**：

```
WebView 内部有两处依赖"创建它的 Context"：
  ① file:///android_asset/ 的解析  → context.getAssets().openNonAsset(...)
  ② Content-Type 的推断            → 见 2.3

如果 WebView 实际用的 Context 是容器的（而不是探针的）：
  ① 去容器的 assets 找 wvprobe.html → ERR_FILE_NOT_FOUND   ✓ 与 F5 一致
  ② 某些 Content-Type 判断走容器的资源/配置 → 可能落回 text/plain  ✓ 与 F2 一致
```

### 2.2 但 F2 还必须解释一件事

**Chromium 的 `FileURLLoaderFactory` 判断 MIME 主要看「文件扩展名」**（`net::GetMimeTypeFromFile`），
`.html` 应该直接判成 `text/html`。

**W2 的 URL 是 `file:///.../wvprobe.html`，扩展名清清楚楚是 `.html`。**

**所以 F2 有两种可能**：

| 可能 | 机制 | 怎么区分 |
| --- | --- | --- |
| **C4-a** | WebView 的 Context/资源不对，导致 `text/html` 这个内置 MIME 映射不可用 | W8/W9/W12 |
| **C4-b** | 容器 hook 改写了路径（沙箱重定向后扩展名丢了，或走的是 `open` 而非按名判断） | W11 里打印 `request.getUrl()` |

### 2.3 待验证的 5 个实验（W8–W12）

| # | 实验 | 判据 |
| --- | --- | --- |
| **W8** | `webView.getContext()` 的 `packageName` / `getClass()`；与 `this.getPackageName()` 比较 | 若 != 探针包名 → **C4-a 成立** |
| **W9** | `MimeTypeMap.getSingleton().getMimeTypeFromExtension("html")` | 若返回 null → **MIME 表被破坏** |
| **W10** | `java.net.URLConnection.guessContentTypeFromName("x.html")` | 若返回 null → **Java 层映射坏了** |
| **W11** | `WebViewClient.shouldInterceptRequest` 里打印 `request.getUrl()` + `request.getRequestHeaders()`，并**主动返回 `new WebResourceResponse("text/html","utf-8", stream)`** | 若这样就能正常渲染 → **修复方案确定** |
| **W12** | `webView.getContext().getAssets() == this.getAssets()`（identityHashCode 比较） | 若不同 → **C4-a 成立** |

**W11 是关键**：它同时回答"根因"和"修复方案"。

---

## 三、修复方案（按 W11 结果分叉）

### 方案 A：如果 W11 证明"自己返回 text/html 就能渲染" ← **最可能、最实用**

**做法**：容器 hook `WebViewClient`，给 `file://` 请求补正确的 MIME。

```kotlin
// core/hook 里新增 WebViewMimeFix
// 思路：不碰 WebView 内部，只在 shouldInterceptRequest 这一层补 MIME
fun installWebViewMimeFix() {
    // 1) hook WebView#setWebViewClient，把用户的 client 包一层
    //    在 wrapper 的 shouldInterceptRequest 里：
    //      - 先问用户的 client
    //      - 用户返回 null 且 url 是 file:// 时，容器自己打开文件并按扩展名给 MIME
}
```

**优点**：
- **不改 WebView 内部**，风险低
- **不改 native hook**
- 对 `loadUrl("file://...")` 直接生效

**缺点**：
- 只覆盖走 `WebViewClient` 的请求（大多数应用都设了 client，覆盖面够）
- 如果应用自己重写了 `shouldInterceptRequest` 并返回了错误 MIME，容器要决定"覆盖还是尊重"

**判定覆盖策略**：容器只在"用户返回 null 且是 file://"时接管。

### 方案 B：如果 C4-a 成立（Context 不对）

**做法**：给 WebView 传正确的 Context。

```kotlin
// hook WebView 的 4 个构造器，把 context 换成 VirtualContext
Class[] ctors = { Context.class, Context.class+AttributeSet, ... };
// 或者在 new WebView 时把 VirtualContext 注入
```

**风险**：WebView 的构造器是 framework 类，hook 它需要 inline hook 或代理子类。
**但注意**：`VirtualContext` 本身已经是 `ContextWrapper`，如果容器给它传的是 `VirtualContext`，
那 `getAssets()` 应该返回 guest 的。**需要 W12 确认到底传的是什么。**

### 方案 C：如果 MIME 表被破坏（W9/W10 返回 null）

**做法**：查是谁破坏的。
- Java 层：容器有没有 hook `MimeTypeMap`？
- native 层：`libc` 的 `stat` 被 hook 后，`MimeTypeMap` 加载 `/system/etc/mime.types` 可能失败

**验证**：`cat /system/etc/mime.types | head` + 探针里读它。

### 明确不做

| 方案 | 为什么不做 |
| --- | --- |
| ~~往 guest 的 AssetManager 用 `addAssetPath` 加 WebView 资源~~ | **已实测导致 Via boot loop（`2fc764d` 回退）** |
| ~~hook WebView 内部 `AwContents`~~ | 风险过高，收益不比方案 A 大 |
| ~~改 `libwebviewchromium`~~ | 影响全局，且 renderer 不在容器内 |
| ~~让 guest 用真实路径（`pathFidelity`）~~ | 真实目录需要真实安装，不可行 |

---

## 四、执行顺序

| 顺 | 动作 | 依赖 | 风险 |
| --- | --- | --- | --- |
| **1** | **修探针 3 个 bug**：W0 后台线程建 WebView、W4 copy 自己覆盖自己、W3 改用 `getAssets()` 直接验证 | 无 | 低 |
| **2** | **加 W8–W12 五个实验** | 1 | 低 |
| 3 | 构建探针 v1.10，跑 W8–W12 | 2 | 低 |
| 4 | 按结果选方案 A/B/C，落盘修改计划 | 3 | 中 |
| 5 | 实现选定方案 | 4 | 中 |
| 6 | 用 Via 主页实测验收 | 5 | — |

**第 1、2 步可以合并成一次探针改动。**

---

## 五、验收标准

| 项 | 判据 |
| --- | --- |
| **W2 修复** | 探针 W2 的 `body` 变成 `WVPAGE_OK hello from probe`（渲染后的 innerText），不再是 HTML 源码 |
| **W3 修复** | `file:///android_asset/wvprobe.html` 能加载 |
| **Via 主页** | 容器内 Via 打开 `homepage2.html` 显示正常页面，不是源码 |
| **Via popup** | 长按文字出现选择菜单，不出现 `Resources$NotFoundException` |
| **无回归** | Via 进程稳定运行 > 60 秒，`am_proc_died` 0 |

---

## 六、时间与设备约定（本文件生效）

**所有时间以手机时间为准。**

- 设备比电脑快约 **1 分 51 秒**（2026-11-24 18:42 实测）
- `logcat` 时间戳 = 设备时间
- 时间切片必须用 `adb shell date '+%m-%d %H:%M:%S.000'` 取设备时间做 `-T`
- 文档里写时间时标注 `(device)` 或 `(PC)`
