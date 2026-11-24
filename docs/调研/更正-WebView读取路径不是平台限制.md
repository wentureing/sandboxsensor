# 更正：删除"平台限制不修"的错误描述，WebView 读取路径重新定性

- 日期：2026-11-24
- 起因：用户指出 —— **"renderer isolated uid 读不到沙箱"和"Android 11+ file:///data/ 限制"这种描述很可能不正确**。
  **Via 在容器外正常，在容器内也应该使用相似读取方式。**
  **也许有正规的读取方式没有被容器开放或者被容器破坏。**
- 结论：**用户的判断正确。我把"容器缺陷"误判成了"平台限制"，这是第 6 次同类错误。**

---

## 一、被更正的两条（原文档 `调研/WebView问题与缺陷映射.md` 的 D1/D2）

### 原 D1（错误）

> **D1：Android 11+ / targetSdk ≥ 30，`file:///data/` 默认禁止**
> 来源：火山引擎文章
> **结论**：真机上是同样的限制 → **不是容器的问题** → **不需要修**

### 原 D2（错误）

> **D2：renderer 是 isolated uid，读不到 guest 的沙箱**
> **归属**：平台安全模型
> **状态**：已知边界，不修

---

## 二、为什么这两条是错的

### 2.1 决定性的反证：**Via 在容器外正常**

用户明确指出：**Via 在容器外主页正常、popup 正常**。

**这直接推翻了"平台限制"的说法**：

```
如果 renderer 真读不到 /data/user/0/mark.via/files/
  → 标准环境 Via 的 file:// 主页也应该失败
    → 但标准环境是正常的
      → 所以"renderer 读不到"这个描述与实际矛盾
```

**同理**：
```
如果 Android 11+ 的 file:///data/ 限制真的让 Via 读不到
  → 标准环境也应该失败
    → 但标准环境正常
      → 所以限制不是主因（Via 自己肯定设了 allowFileAccess = true，或者用了别的加载方式）
```

### 2.2 Chromium 的架构也不支持"renderer 读文件"

**Chromium 的 `file://` 由 browser 进程的 `FileURLLoaderFactory` 处理**，不是 renderer：

```
guest 进程（browser process）
  ├─ WebView / AwContents（Java）
  ├─ AwContentBrowserClient（native，在 guest 进程）
  │    └─ URLLoaderFactory
  │         └─ FileURLLoaderFactory   ← file:// 的读取在这里（guest 进程）
  └─ Mojo IPC
       ↓
renderer 进程（isolated uid）
  └─ 只负责 HTML 解析 / JS 执行 / 布局渲染
     └─ 需要子资源时通过 Mojo 请求 browser 去加载
```

**所以 `file://` 读文件用的是 guest 进程的权限** → **容器 hook 会重定向它** → **应该能读到沙箱**。

### 2.3 所以正确的定性是

| 维度 | 原描述（错误） | **更正后** |
| --- | --- | --- |
| **谁读文件** | renderer（isolated） | **browser 进程（= guest 进程）** |
| **为什么容器内失败** | "平台限制，不修" | **待查：容器没开放 / 破坏了正规读取方式** |
| **Android 11+ 限制** | "真机同样限制" | **Via 已经处理了（它设了 `allowFileAccess` 或用了别的加载方式）** → **不是主因** |
| **该不该修** | **不修** | **该修**（但先查清是"没开放"还是"被破坏"） |

---

## 三、"被容器开放 / 破坏"的六种可能（都要查，不要预设结论）

### 3.1 容器"没开放"的可能

| # | 可能性 | 怎么查 |
| --- | --- | --- |
| **N1** | `VirtualContext` 没正确实现 `getAssets()` / `getResources().getAssets()` | 探针加 `P35`：`getAssets().open("xxx")` |
| **N2** | guest 的 `Resources` 里没有 WebView 资源（**已在 `23b0538` 修**） | `logcat \| grep "Added WebView resources"` |
| **N3** | `AwSettings.allowFileAccess` 被容器改成了 `false` | 查 `PackageManagerProxy` / `VirtualContext` 是否碰 `targetSdkVersion`（**已确认没碰**） |
| **N4** | guest APK 的 `targetSdkVersion` 被容器报成了 35（容器的）而不是 Via 的 36 | **已确认**：`VirtualContext.getApplicationInfo()` 用 `super`（容器的 35）→ **但 35/36 都 ≥ 30，`allowFileAccess` 默认都 false** → **不是这个** |

### 3.2 容器"破坏"的可能

| # | 可能性 | 怎么查 |
| --- | --- | --- |
| **B1** | 容器 hook 把 WebView 的 `open()` **重定向到了沙箱里不存在的路径** | 探针 `N03`/`N06` + `NextVM-Native` 的 `open:` 日志 |
| **B2** | **`redirect_path` 的规则把 `file://` 的路径改写错了** | 查 `setupAppRedirections` 对 `/data/user/0/mark.via/` 的映射 |

### 3.3 **必须先排除的一种可能**

**Via 可能根本不用 `file://`** —— 它可能用：
- `loadDataWithBaseURL(baseUrl, html, "text/html", "utf-8", null)`（reader 模式）
- 或 `WebViewAssetLoader`（`https://appassets.androidplatform.net/...`）
- 或 `android_asset`

**如果 Via 用 `loadDataWithBaseURL`**，那**根本不需要 file 读取** → **容器内失败就是别的原因**（如 HTML 内容被截断、MIME 参数错、JS 被拦）。

**所以第一件事是确认 Via 的加载方式**（受控测试 `WebViewProbeActivity` 用三种方式对照）。

---

## 四、更正后的计划位置

| # | 项 | 原位置 | **新位置** |
| --- | --- | --- | --- |
| D1 | Android 11+ `file:///data/` | "平台限制，不修" | **已知背景**（Via 已处理）→ **不列为缺陷，但也不是"不修的缺陷"** |
| D2 | renderer isolated uid | "平台安全模型，不修" | **❌ 删除这条描述** —— **读取者是 browser 进程，不是 renderer** |
| **新 C3** | **容器的 WebView 读取方式未开放/被破坏** | — | **`计划-修正与受控测试方案.md` §四（`WebViewProbeActivity`）** ← **这是真正要查的** |

---

## 五、受控测试要回答的三个问题（补充）

原来 `WebViewProbeActivity` 的设计只测"能不能加载"，现在要加**三项对照**：

| # | 加载方式 | 用途 |
| --- | --- | --- |
| **1** | `loadUrl("file://" + getFilesDir() + "/wvprobe.html")` + `allowFileAccess=true` | **测"容器内的 file:// 读取"** |
| **2** | `loadDataWithBaseURL("file://" + getFilesDir(), html, "text/html", "utf-8", null)` | **测"Via 可能用的 reader 模式"** |
| **3** | `loadDataWithBaseURL("https://localhost/", html, ...)` | **测"完全不用 file 协议"** |

**判据**：
- **1 失败、2/3 成功** → **容器破坏了 `file://` 读取**（B1/B2）
- **1/2/3 都失败** → **容器破坏了 WebView 本身**（资源/渲染）
- **1 成功** → **Via 的问题不在 WebView**

---

## 六、我要记录的教训（第 6 次）

| 次 | 错误 | 正确做法 |
| --- | --- | --- |
| 1 | `N03`/`N06` 失败 → "重定向生效" | 判据要选"真实路径存在、沙箱不存在"的样本 |
| 2 | `enableAntiDetection` 搜不到 → "死代码" | 静态搜索 ≠ 运行时观测 |
| 3 | `syscall` 只打日志 → "删掉" | 能力即使当前不用也可能为未来准备 |
| 4 | `Log.wtf` 出现 → "必然失败" | 日志严重级别 ≠ 业务结果 |
| 5 | 函数名含 "Via" → "Via 专属" | 命名可能只是介词 |
| **6** | **标准环境正常 + 容器内异常 → "平台限制/不修"** | **标准环境正常就是"容器缺陷"的反证** —— **不能用"平台限制"掩盖** |

**第 6 次的特殊性**：
> **我把"我查不到原因"包装成了"平台限制，不修"。**
> **这是一个比"猜错"更糟的错误** —— **它主动关闭了继续调查的可能。**

---

## 七、一句话

> **Via 在容器外能读的东西，在容器内也应该能读。**
> **如果读不到，那是容器的问题 —— 不是平台限制。**
> **`renderer isolated uid` 这条描述要删除**（读取者是 browser 进程）。
