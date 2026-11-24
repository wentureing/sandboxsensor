# 修改（已实施）：WebView `file://` 代读 + 补 MIME（v137-webview-file）

- 日期：2026-11-24
- 仓库：`wentureing/ShitJumpingVM`（容器）
- 依据：
  - `docs/records/记录-WebView根因定位-W14沙箱路径.md`（容器内 MIME 走内容嗅探）
  - `docs/records/记录-Gaia内WebView对照-W2正常与容器差异.md`（Gaia 同路径形态正常 → 我们容器的缺陷）
  - `docs/架构/架构-代理范式.md` §五：「guest 读 WebView 的 `file:/` → 只能代读，且需找注入点」
- 性质：用户已说「开始编码」，本文是**事后补录的计划 + 实际变更**（同一文件）

---

## 一、目标

让 guest 的 WebView 用 `file://` 加载自己的 HTML/CSS/SVG 时拿到**正确的 MIME**，
不再被当纯文本显示源码（Via 主页现象），且 asset 也能打开（Via 的 `logo.svg`）。

---

## 二、为什么是这一层（不是改路径、不是改 ContextUtils）

| 证据 | 结论 |
| --- | --- |
| 容器内 `.html` 内容改成 `GIF89a` 开头 → `document.contentType=image/gif` | MIME 来自 `guessContentTypeFromStream` 内容嗅探 |
| `/data/local/tmp` 与 Gaia 沙箱都正常 | 不是「沙箱路径」的普遍行为，是我们容器独有的 |
| `getAllApkPaths`、`W8/W12` 全正常 | 不是 Context/Assets 身份问题导致的 MIME 表损坏 |

按 `架构-代理范式.md`：
- 元数据（MIME）可以**造假**，属于「查询类」，范式中最安全的一类；
- 字节流必须有真实载体 → 用 `FileInputStream` / `assets.open()`；
- 所以正确做法就是**代读 + 给元数据**，即 `shouldInterceptRequest` 这一层。

**刻意没做**：
| 方案 | 为什么不做 |
| --- | --- |
| 伪造 `ContextUtils.sApplicationContext`（方案 C） | 改的是进程级静态字段，会让 WebView 内部 ContentResolver/SP/DB 路径一起变；且「WebView 认为是 guest、进程实际是容器」违反「假代理必须成组一致」 |
| 把 guest 的 `getFilesDir()` 换成 `/data/local/tmp` 前缀 | `hooked_opendir()` 没有重定向逻辑 → 目录枚举会坏 |
| 全量接管所有 `file://` | 媒体/PDF 走原生支持 Range（seek），我们返回整条流不支持 → 拖动会坏 |

---

## 三、三条硬规则（性能 / 功能 / 暴露）

### 3.1 性能（可忽略）

`file://` 请求**本来就经过 Java 层**（探针 W2 日志里 `shouldInterceptRequest` 被调用过），
所以包装不改变请求流向，增量只有「`new File` + `isFile()` + MIME 查表 + 开流」：

| 项 | 数值 |
| --- | --- |
| 单次增量 | ~20–50 μs |
| Via 主页（约 5 个请求） | **约 0.2–0.5 ms**（一页渲染 200–800 ms） |
| 普通保存版网页（30–300 请求） | 1–15 ms |

### 3.2 功能

| 规则 | 原因 |
| --- | --- |
| **只接管白名单扩展名**：`html htm xhtml css js mjs json svg txt xml webmanifest ico` | 文本 + 矢量/小图；不涉及 seek |
| **媒体/PDF/压缩包不接管**（`mp4 mp3 webm pdf zip …`） | 原生支持 Range，我们整条流不支持 seek |
| **任何异常 / `!isFile()` / MIME 查不到 → `return null`** | 保证「最差等于现状」，保存版网页里的 woff2/wasm/avif 等不会比现在更坏 |
| **不碰 `/system` `/apex` `/vendor` `/data/app` `/product`** | 那是 WebView 自己的资源区，不属于 guest |
| **网络视频完全不受影响** | 视频走 http/https（m3u8/ts/mp4 直链/DASH/MSE），`scheme != file` 直接 return null |

### 3.3 暴露（会不会被应用发现）

**唯一的硬暴露**：`WebView.getWebViewClient()` 返回我们的 `DelegateClient`，不是应用设的那个。

| 评估 | 结论 |
| --- | --- |
| Via 会不会检查 | 几乎不会 —— Via 是轻量浏览器，不做反注入断言 |
| 其他可观察差异 | MIME 与真机**一致**（都按扩展名）；URL/baseURI 不变；时序差 ~μs 不可测；`document.lastModified` 可能为空（轻微） |
| 将来的金融/政务 App | 可能检查 → 用 `VirtualConstants.webviewFileDelegateEnabled()` 关掉 |

---

## 四、影响范围

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `core/virtualization/.../lifecycle/WebViewFileDelegate.kt` | **新增**（约 380 行） | 视图树扫描 + client 包装 + `file://`/asset 代读 + MIME 白名单 |
| `core/virtualization/.../lifecycle/VirtualApplicationManager.kt` | 修改（+9 行） | `onActivityResumed` 与 v124 定时器各加一次 `WebViewFileDelegate.scan(...)` |
| `core/model/.../ProcessSlot.kt` | 修改（+21 行） | `VirtualConstants.webviewFileDelegateEnabled()` 开关（默认开） |

**不改**：native hook、WebView 构造、AssetManager、`ContextUtils`、Via 任何代码。

---

## 五、实现要点（为什么这么写）

1. **先问应用，再接管**
   ```kotlin
   val fromApp = orig?.shouldInterceptRequest(view, request)
   if (fromApp != null) return fromApp        // Via 处理 homepage.css 走这里
   if (scheme != "file") return null
   ```
   与 Via 零冲突：它只对 `homepage.css` 返回非 null，其余返回 null。

2. **client 注入不用扫字段，走 `getWebViewClient()`**
   探针 W30 证明「按类型扫 `WebView.declaredFields`」在 Android 15 上**找不到**字段
   （client 存在 provider 内部）。所以 `currentClient()` 三级 fallback：
   `getWebViewClient()`（反射）→ 字段名 `mWebViewClient` → 放弃（不接管）。

3. **转发必须全**
   包装 client 唯一的回归风险是「漏转发应用覆写的方法」= 丢掉那个行为。
   所以按 API 35 的公开方法逐个转发（`onPageStarted/Finished`、`shouldOverrideUrlLoading`
   两个重载、`onReceivedError` 两个重载、`onRenderProcessGone`、`onReceivedSslError` …），
   每个转发都包在 `safe { }` 里 —— 应用的 client 抛异常不能影响我们。

4. **幂等 + 逃逸兜底**
   - `DelegateClient` 自身判断：已是本类型就不再包；
   - `wrappedOriginals`（WeakHashMap 集合）防止同一原实例被包两层；
   - 应用若在 resume 之后又 `setWebViewClient`，v124 定时器每 5s 复查并补回
     （该定时器只跑 10 分钟，窗口期内仍是现状行为，不会更坏）。

5. **charset 传 null**
   让 WebView 按内容/`<meta charset>` 自己判定，尽量贴近原生行为（不硬塞 utf-8）。

---

## 六、风险与回退

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 漏转发某个应用覆写的方法 | 已按 API 35 全量转发；Via 实测验收 |
| 2 | `getWebViewClient()` 在本 ROM 上不可用 | 有字段名 fallback；两者都失败则不接管（等于现状），`GuestBreadcrumb` 会显示 `installed=0` |
| 3 | 应用在 10 分钟后换 client → 逃逸 | 已知限制，退化为现状行为 |
| 4 | 某些 ROM `MimeTypeMap` 缺项 | 有 `MIME_FALLBACK`；查不到就 return null 交给原生 |

**回退**（不改代码即可关闭）：
```kotlin
// 容器在 fork guest 前设置
System.setProperty("nextvm.webviewFileDelegate", "0")
```
**代码回退**：`git -C _ref/ShitJumpingVM revert <sha>`。

---

## 七、验证方法

```powershell
# 1) 容器内探针：W2/W3/W4/W15 应变成 text/html；W16(.txt) 仍 text/plain
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.UNINSTALL --es pkg com.sandbox.probe
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path /data/local/tmp/probe-v113.apk
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH_ACTIVITY --es pkg com.sandbox.probe --es activity com.sandbox.probe.WebViewProbeActivity
adb shell run-as com.nextvm.app cat files/virtual/data/<inst>/files/wvprobe-result.txt

# 2) breadcrumb 应出现 v137 标记
adb logcat -d -b all | Select-String "v137-webview-file"

# 3) Via 主页渲染（不是源码），且进程 60s 存活
adb shell pidof mark.via
adb exec-out screencap -p > docs/records/img/via-home-after-v137.png
```

**判据**：
- W2/W4/W15 → `ct=text/html` 且 body 是渲染后的 innerText
- W3 → `ASSET_OK`（不再是 EMPTY）
- W16 → `ct=text/plain`（证明我们是**按扩展名**给 MIME，不是无脑 text/html）
- Via 主页显示正常页面；`logo.svg` 显示出来
- `v137-webview-file installed>=1 delegated>=1`

---

## 实际变更

- 新增 `WebViewFileDelegate.kt`
- 修改 `VirtualApplicationManager.kt`（2 处接线）
- 修改 `ProcessSlot.kt`（开关）

与本文计划一致；构建与设备验证结果见后续追加。
