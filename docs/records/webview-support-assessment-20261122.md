# 评估：WebView 特殊支持的工作量、改动点、架构影响

- 日期：2026-11-22
- **存档点：tag `pre-webview-support` = `a528e09`**（已推远端）
- 前置结论（见 `webview-proxy-direction-20261122.md`）：WebView 渲染在 `webview_zygote` 派生的 isolated 进程，容器 hook 覆盖不到

---

## 一、先看清现状：容器对 WebView 已经做了什么

| 文件 | 行数 | 实际职责 |
| --- | ---: | --- |
| `WebViewWarmup.kt` | 404 | **修复初始化崩溃**：① 反射调 `WebViewFactory.getProvider()` 提前缓存；② 修复 guest `LoadedApk` 的 `dataDir`（缺失会导致 provider 构造时 `getSharedPreferences` 抛 "No data directory found"）；③ 处理 "Already registered a list of actions" 双重注册 |
| `WebViewWatch.kt` | 170 | **纯诊断**：40ms 采样主线程栈，记录 WebView 初始化过程，输出 `webviewwatch-<pid>.log` |

**换句话说：WebView 能启动这件事，容器已经解决了。** 缺的是「`file://` 加载本地页能正常渲染」。

---

## 二、四种实现方案与工作量

### 方案 A：Java 层 hook `WebView`（❌ 不可行）

想做的事：拦截 `WebView.setWebViewClient` 或 `loadUrl`，套一层 `WebResourceResponse`。

**为什么不行**：

| 障碍 | 说明 |
| --- | --- |
| `android.webkit.WebView` 是 **framework 类** | 由 boot classloader 加载，容器的 `VirtualClassLoader` 插不进去 |
| `WebView` **没有 `getWebViewClient()`** | guest 一旦设了自己的 client，无法再包一层 |
| 字节码插桩只能作用于 **guest 自己的 dex** | 改不了 framework |

**工作量**：不适用（做不到）。

### 方案 B：native hook `android_webview` 的 JNI（⚠️ 理论可行、代价高）

`WebView.loadUrl` 最终走 JNI 到 `libwebviewchromium`。可以在 **guest 主进程**里 hook 这些 JNI 入口。

| 项 | 评估 |
| --- | --- |
| 改动位置 | `core/hook`（新增 JNI 符号 hook） |
| 工作量 | **3–5 天**（要逆向 `libwebviewchromium.so` 的 JNI 符号、处理 ABI、验证不破坏正常网络加载） |
| 成功率 | **低-中**。JNI 方法不是导出的 C 符号，需要扫描 `JNIEnv->RegisterNatives` 或 ART 的 method 表 |
| 关键阻塞 | 即使 hook 成功，**渲染仍在 isolated 进程**——hook 的是"发起请求"的进程，不是"读文件"的进程 |
| 风险 | 高（WebView 内部结构随版本变化） |

**结论：不做。**

### 方案 C：替换 WebView 内核（X5/TBS）（⚠️ 可行但重）

腾讯 X5 支持单进程模式，且是应用级 SDK（不依赖系统 WebView）。

| 项 | 评估 |
| --- | --- |
| 改动位置 | `app`（初始化 SDK）+ 新增依赖（约 30–60 MB） |
| 工作量 | **3–5 天**（集成 + 适配 `WebView` 到 `X5WebView` 的替换 + 回归） |
| **架构影响** | **大**：guest 代码里 `new WebView(ctx)` 拿到的还是系统 WebView，除非做 **类替换**（把 guest 的 `android.webkit.WebView` 换成 X5 的）—— 而这是 framework 类，做不到 |
| 关键阻塞 | **同样的框架类问题**：X5 提供的是 `com.tencent.smtt.sdk.WebView`，guest 不会用 |

**结论：不做。**除非目标应用本身就用 X5。

### 方案 D：只支持"容器自己可控的 WebView 场景"（✅ 可行、成本低）

不碰 guest 的 WebView，而是：

1. **容器自己的 UI**（如果有 WebView）用完整代理
2. **给 guest 提供"路径提示"**：通过 `ApplicationInfo` 让 guest 的 `getFilesDir()` 返回**真实可读路径**（如 `/sdcard/Android/data/<容器>/...`）
   - 这样 Via 的 `file://` 会指向沙箱外部可读位置
   - **但**：Android 12 上 WebView isolated 进程访问 `/sdcard` 被 denied（你已实测）
   - **在 Android 15 上可能可行**（你实测 Android 15 能访问 `/sdcard/Download`）

**工作量**：2 小时（改 `ApplicationInfo.dataDir` 返回路径）

**风险**：把 guest 数据放到公共可读位置，**暴露面显著增大**（其他应用能读 guest 数据）

---

## 三、架构影响评估

| 方案 | 需要新模块 | 需要改动的现有模块 | 对依赖图的影响 |
| --- | --- | --- | --- |
| A | — | — | 不可行 |
| B | `core/hook` 内新增 | `core/hook` | 小（hook 层内聚） |
| C | `app` + 依赖 | `app` | **大**（引入第三方 SDK，`app` 依赖膨胀） |
| D | 无 | `core/virtualization`（`ApplicationInfo` 构造） | **极小** |

**方案 B/C 都要花 3–5 天，且成功率不高；方案 D 只要 2 小时但有安全代价。**

---

## 四、回答你的两个问题

### Q1：工作量、修改位置、架构影响

| 方案 | 工作量 | 位置 | 架构影响 | 建议 |
| --- | --- | --- | --- | --- |
| A. Java hook | 不可行 | — | — | ❌ |
| B. native JNI hook | 3–5 天 | `core/hook` | 小 | ❌ 成功率低 |
| C. 换 X5 内核 | 3–5 天 | `app` + 依赖 | **大** | ❌ 框架类问题堵死 |
| D. 让 guest 数据落到公共路径 | **2 小时** | `core/virtualization` | **极小** | ⚠️ 安全代价大 |

### Q2：为了支持 WebView 轻度修改架构、减轻耦合，值不值？

**这是两件不相干的事，混在一起会做错决定：**

| 目标 | 是否需要改架构 | 结论 |
| --- | --- | --- |
| **支持 WebView 的 `file://`** | **不需要**，也不该通过"改架构"来做 | 改架构解决不了（framework 类不可替换） |
| **减轻 `VirtualEngine` 的双向耦合** | 需要（抽接口） | 独立的技术债，与 WebView 无关 |

**所以：**

> **为了 WebView 去改架构 → 不值**（改架构解决不了 framework 类不可替换的根本问题）。
> **减轻耦合 → 值得做，但应该作为独立的重构任务**，不要和 WebView 绑在一起。

---

## 五、一个必须先验证的前提（否则整个方向可能是空的）

你说"应用隐藏大师（Gaia）能正常加载 webview 和 via"。

**需要确认「正常」的具体含义**：

| 待验证 | 方法 |
| --- | --- |
| Gaia 里的 Via **主页**是否正常渲染（不显示源码）？ | 在 Gaia 里打开 Via，看主页 |
| Gaia 里的 Via **长按文字**是否出现菜单且可交互？ | 在 Gaia 里长按 |
| Gaia 里 WebView 能否访问 `/sdcard/Download`？ | 在 Gaia 的 Via 里打开 `file:///sdcard/Download/` |

**如果 Gaia 里的 Via 主页也是源码** → 那"Gaia 能正常"只是"能启动"，我们不需要追这个方向。
**如果 Gaia 里主页正常** → 说明它确实有额外机制，值得逆向（反编译找 `shouldInterceptRequest`）。

**这个验证只要 5 分钟**，但能决定后面 3–5 天的投入是否值得。

---

## 六、建议

1. **先验证 Gaia 里 Via 主页的真实表现**（5 分钟）
2. **如果 Gaia 也显示源码** → 接受 WebView 限制，写进已知边界，不再投入
3. **如果 Gaia 正常** → 逆向它（找 `shouldInterceptRequest` / 自研 client 注入），再评估是否复制
4. **耦合重构独立立项**，不与 WebView 绑定

**当前不做任何代码修改。存档点 `pre-webview-support` 已就位。**
