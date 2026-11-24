# 错误结论归档（2026-11）

> **这里的文档不是"没用了"，而是「结论已被实测推翻」。**
> 保留它们是为了：① 记住错在哪，避免重犯；② 保留当时的证据与推理过程。
> **不要拿这里的任何结论当依据**；每条都标明了"被什么推翻"和"现在的正确结论在哪"。

---

## 一、归档清单

| 文档 | 原结论 | 被什么推翻 | 现在看哪里 |
| --- | --- | --- | --- |
| **`交接-20261124-WebView调查.md`** | 其"下一步建议"认为「改路径映射就能修好 MIME，完全不碰 WebView」 | 探针 **W20**（内容改成 `GIF89a` 得到 `image/gif`）+ **W34**（`AndroidProtocolHandler.open()` 对沙箱与 `/data/local/tmp` **都返回 null**）→ MIME 判定不在 Java 层，路径映射改不到它 | `docs/输出/记录/记录-W2-text-plain机制收敛.md` |
| **`记录-Gaia机制分析.md`** | §三「Gaia 让 Via 主页正常的机制：**推断为宿主代读**（`shouldInterceptRequest`）」 | 探针在 Gaia 内实测：**W2 = `text/html`、W3 = `ASSET_OK`** —— Gaia 什么都没 hook 就正常。它是**路径保真**（`gaia/data/user/0/<pkg>`），不是代读 | `docs/输出/记录/记录-Gaia内WebView对照-W2正常与容器差异.md` |
| **`记录-WebView问题全链路.md`** | 含「沙箱该迁到外部存储」「renderer 读不到别的应用目录所以必须代读」等一串假设 | 实测：Gaia 沙箱在**内部存储**却做到正常；容器内 `file://` 的内容**确实被读到了**（只是 MIME 错）；W34 证明 Java 侧完全不参与 | `docs/输出/记录/记录-W2-text-plain机制收敛.md`、`docs/架构/架构-路径转换机制摸底.md` |
| **`20261124-webview-file-delegate.md`**（v137 方案：包装 `WebViewClient` 补 MIME） | 「先问应用 client，返回 null 就补 MIME」可行 | **实测时序不可能**：Via 在 `onCreate`/fragment 里就 `loadUrl`，而视图树扫描最早到 `onResume`/定时器 → `installed=0 (+132ms)` → `installed=1 (+10s)`，**永远晚一步**（`delegated=0`）。已 **revert** | `docs/输出/记录/记录-W2-text-plain机制收敛.md` |
| **`20261124-webview-mime-fix.md`**（方案 A 的注入点写法） | 「按类型扫 `WebView.declaredFields` 就能拿到 `WebViewClient`」 | 探针 **W30** 在 Android 15 上直接 `FAIL: WebView 里找不到 WebViewClient 类型字段`（client 存在 provider 内部） | 同上 |
| **`P1-3-WebView代读.md`** | 前提是「**等 Gaia 机制研究清楚后再做**代读」 | Gaia 机制已查明（路径保真、无需 hook）→ 这个前提消失；而"代读"本身仍是对的方向（W11 验证有效），但注入点/时机问题未解 | `docs/架构/架构-注入时机审计.md`、`docs/架构/架构-Java层路径重定向-施工方向与方案.md` |

---

## 二、这批错误有共同模式（值得记住）

| # | 模式 | 例子 |
| --- | --- | --- |
| 1 | **从局部信号直接下结论** | 看到 W2 显示源码 → 直接推"renderer 读不到文件"（其实读到了，是 MIME 错） |
| 2 | **把"别人的实现"想当然** | 想当然认为 Gaia 必然是"宿主代读"（实测它压根不需要） |
| 3 | **没控制变量的对比** | 早期把 W14 正常 / W2 异常归因于"路径前缀"，其实当时 **BOM 也没排掉**（W35 四组合才排掉） |
| 4 | **在错误的位置找原因** | 花了多轮在 Java 侧（`AndroidProtocolHandler`）找 MIME，W34 一次就证明它不参与 |
| 5 | **把补丁当修复** | v137（client 包装）→ v139（假 dataDir）→ v140（改 VirtualContext），连锁补丁，最后全部回退 |

**可迁移的做法**（已写进 `AGENTS.md` 的文档规范）：
- 对比实验必须**控制变量**（一次只动一个）
- 先找**原因判据**（谁在读这个值），再找**后果判据**（页面表现）
- 任何"某组件不需要额外处理就正常"的观察，都要作为**架构线索**记下来，而不是当成巧合

---

## 三、引用这些文档时的正确姿势

如果你在别处看到下面这些说法，**它们已经过时**：

| 过时说法 | 现在的说法 |
| --- | --- |
| "WebView 渲染在 isolated 进程，容器 hook 覆盖不到 → 只能代读" | 容器内 `file://` 的内容是**读得到**的；问题是 **MIME 判定**（走 native，与容器 hook 无关） |
| "Gaia 靠宿主代读让 Via 主页正常" | Gaia 靠**路径保真**，不需要任何 hook |
| "把沙箱迁到外部存储可以解决" | 前提被推翻：Gaia 在**内部存储**也正常 |
| "补 MIME 只要包装 WebViewClient" | 时序上做不到（`loadUrl` 早于任何视图树扫描） |
| "MIME 来自 Java 层嗅探" | 仅对 v1.11 时期的某条路径成立；W34 证明普通 `file://` 的 Java `open()` 返回 null |
| "封装的 `FileAccessInterceptor` 已在用" | **零调用**（见 `docs/现状/零调用扫描报告.md`）；v141 另写了 native 实现 |
