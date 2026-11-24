# WebView 相关问题与缺陷清单的映射，及在计划中的位置

- 日期：2026-11-24
- 起因：用户问 —— **WebView 预计和什么缺陷相关，当前在计划什么位置**

---

## 零、一句话

> **WebView 相关问题分散在 4 个不同类别里，不能当成一个问题看：**
> **①资源缺失（已修）②监控未生效（零调用类）③行为未定论（需受控测试）④平台限制（不是缺陷）**

---

## 一、A 类：已确认并已修（`23b0538`）

### A1：`ensureWebViewResources` 零调用 → popup 崩溃

**证据链**（实测 logcat）：

```
17:19:42.336 E cr_JniAndroid: Handling uncaught Java exception
android.content.res.Resources$NotFoundException: Resource ID #0x20c0006
    at android.content.res.Resources.getInteger(Resources.java:1320)
    at WV.L5.onCreateActionMode(chromium-TrichromeWebViewGoogle6432...:52)
    at com.android.internal.policy.DecorView.startActionMode(DecorView.java:1131)
    at android.view.ViewGroup.startActionModeForChild(ViewGroup.java:1072)
```

**因果**：
```
长按文字 → DecorView.startActionMode（系统）→ WebView 的 onCreateActionMode
  → 读自己的资源 #0x20c0006 → guest 的 Resources 里没有 → 抛异常
    → chromium jni_android.cc(158) "Crashing due to uncaught Java exception" → crash
```

**归属**：**「实现了但没接线」类**（与 `AntiDetectionEngine`、Settings 开关同类）
**状态**：✅ 已修（`23b0538`），⏳ 待 CI 验证

### A2：`WEBVIEW_APK_PATHS` 过时

**证据**：
```
原来 4 条：
  /system/app/WebViewGoogle/WebViewGoogle.apk
  ...
设备实际：
  /product/app/WebViewGoogle64/WebViewGoogle64.apk          ← 缺 64 后缀
  /product/app/TrichromeLibrary64/TrichromeLibrary64.apk    ← 145 MB，资源在这里，完全没列
```

**归属**：**「过时的硬编码清单」类**
**状态**：✅ 已修（同提交），**已做 v156/Trichrome 双向兼容**

---

## 二、B 类：已确认未修

### B1：`WebViewWatch` / `WebViewWarmup`（574 行）未生效

**代码位置**：
```kotlin
// VirtualApplicationManager.kt:524-525，在 if (fwApp != null) 分支里
WebViewWatch.schedule(packageName, fwApp.filesDir)
WebViewWarmup.schedule(packageName, fwApp.filesDir)
```

**矛盾**：
- `fwApp` **实测非空**（breadcrumb：`framework makeApplication exit app=mark.via.BrowserApp`）
- → **应该执行**
- → **但 logcat 里 `WebViewWatch`/`WebViewWarmup` 零命中**

**两种解释**（未定论）：
1. `runWatch`/`runWarmup` **内部不打日志**（只写文件）
2. 或 **`schedule` 真的没被调用**（条件分支另有问题）

**归属**：**「零调用」类**（结果上零日志）
**计划位置**：`docs/调研/零调用扫描报告.md` **§4.3**
**状态**：❌ **待查**（成本低：加一行日志）

---

## 三、C 类：未定论，需受控测试

### C1：Via 主页显示源码

**已有的三个观测**（都指向不同方向）：

| # | 观测 | 来源 |
| --- | --- | --- |
| 1 | 截图：UI 只有 **2 个 `TextView`**（`homepage2.html` / `1`），**没有 WebView 控件** | `probe-reports/via-screen.png` |
| 2 | `E chromium: Renderer process (13639) crash detected (code -1)` + `am_kill: [..., isolated not needed, ...]` | logcat |
| 3 | Via 的 `mark.via.Shell` **只活 1188ms** 就被 `finish-imm:idle` 销毁 | BootCrumb 时间线 |

**三种可能**（**互相排斥，必须用受控测试区分**）：

| 可能 | 判据 | 若成立说明 |
| --- | --- | --- |
| **① Via 自己用 `TextView` 显示** | UI 里没有 WebView 实例 | **不是容器问题**，是 Via 的行为 |
| **② renderer 真的崩** | renderer 有独立的 FATAL/tombstone | 容器 hook 污染了 renderer（但 renderer 不接受容器 hook） |
| **③ Activity 被提前 finish** | `finish-imm:idle` 有明确的调用者 | **容器的启动流程问题** |

**关键**：**观测 2 的 `code -1` 已被判定为噪音**（`am_kill: isolated not needed` = 宿主销毁后的正常回收），
**所以 ② 的概率最低**。

**归属**：**「行为未定论」类**
**计划位置**：`docs/计划-修正与受控测试方案.md` **§四（`WebViewProbeActivity` 受控测试）**
**状态**：❌ **待执行**

### C2：`finish-imm:idle` 打断启动

**时间线**：
```
17:19:45.654  instr callActivityOnCreate enter mark.via.Shell
17:19:45.793  cb onActivityResumed                     ← 前台
17:19:46.781  cb onActivityPaused                       ← 1188ms
17:19:46.810  wm_destroy_activity: [.stub.P0$Standard00, finish-imm:idle]
17:19:46.825  cb onActivityDestroyed
```

**`finish-imm:idle`** 说明**有代码调了 `finish()`**（不是崩溃，也不是 OOM）。

**候选调用者**（待查）：
- 容器的 `VirtualActivityManagerService.finishActivity`（L217）
- 或用户操作（返回键）
- 或 Via 自己

**归属**：**「行为未定论」类**
**计划位置**：**尚未单独立项**（应并入 C1 的受控测试）
**状态**：❌ **待查**

---

## 四、D 类：平台限制（**不是缺陷**）

### D1：Android 11+ / targetSdk ≥ 30，`file:///data/` 默认禁止

**来源**：[Android 11（target SDK 30）WebView 加载内部存储 HTML 报 ENOENT](https://www.volcengine.com/article/3358238)

> **Android 11+ 上 Target SDK ≥30 时，WebView 默认禁止直接通过 `file:///data/` 前缀的路径
> 访问应用内部私有存储文件，就算文件实际存在也会抛出 ENOENT 错误。**

**关键**：**真机上是同样的限制** → **Via 作为浏览器肯定自己设了 `allowFileAccess = true`**。

**所以**：**这不是容器的问题**，**不需要修**。

### D2：renderer 是 isolated uid，读不到 guest 的沙箱

**事实**：renderer 的 uid 是 99497/99498/99499（isolated），**由 `webview_zygote` fork**，
**不属于 Via 也不属于容器**。

**影响**：
- **`file:///android_asset/`** → 走 `AssetManager`（Binder 到 `Resources`）→ **不受限** ✅
- **`file://<guest 私有目录>`** → renderer 按自己的 uid 判权限 → **读不到**（如果读取在 renderer）

**归属**：**平台安全模型**
**计划位置**：`docs/架构/架构-代理范式.md` 的「代理不能解决的」
**状态**：**已知边界，不修**

---

## 五、汇总表：WebView 问题 × 缺陷类别 × 计划位置 × 状态

| # | 问题 | 缺陷类别 | 计划位置 | 状态 |
| --- | --- | --- | --- | --- |
| **A1** | `ensureWebViewResources` 零调用 → popup crash | **零调用** | `23b0538` | ✅ 已修，⏳ 待验 |
| **A2** | `WEBVIEW_APK_PATHS` 过时（缺 Trichrome） | **过时清单** | `23b0538` | ✅ 已修，⏳ 待验 |
| **B1** | `WebViewWatch`/`Warmup` 574 行无日志 | **零调用** | `调研/零调用扫描报告.md` §4.3 | ❌ 待查（成本低） |
| **C1** | Via 主页显示源码 | **行为未定论** | `计划-修正与受控测试方案.md` §四 | ❌ 待执行受控测试 |
| **C2** | Via Shell 1188ms 被 `finish-imm:idle` | **行为未定论** | 并入 C1 | ❌ 待查 |
| **D1** | Android 11+ `file:///data/` 限制 | **平台限制** | `架构/架构-代理范式.md` | 不修 |
| **D2** | renderer isolated uid 读不到沙箱 | **平台安全模型** | 同上 | 不修 |

---

## 六、回答"WebView 预计和什么缺陷相关"

### 6.1 与本轮已归类缺陷的关系

| 缺陷类别（本轮已建立） | WebView 是否涉及 |
| --- | --- |
| **①「实现了但零调用」** | ✅ **A1（已修）、B1（待查）** |
| **②「过时的硬编码清单」** | ✅ **A2（已修）** |
| **③「精确匹配 vs 前缀匹配」** | ❌ 不涉及（那是 `is_path_hidden`） |
| **④「行为未定论」** | ✅ **C1、C2** |
| ⑤ 平台限制 | ✅ D1、D2（**不修**） |

**所以**：**WebView 的问题横跨 3 个类别（①③④），其中 ①和②已经修好，④还需要受控测试。**

### 6.2 一个重要的判断

> **A1 的修复（资源接线）可能已经解决了"popup 崩溃"，但"主页显示源码"是另一个问题。**
>
> **因为**：
> - A1 的崩溃点是 `onCreateActionMode`（**长按文字**）
> - 主页显示源码是 **URL 加载 / 渲染**（另一个路径）
> - **两者不一定是同一个根因**

**所以**：**需要 C1 的受控测试来区分**。

---

## 七、建议的下一步（WebView 相关）

| 顺 | 动作 | 成本 | 收益 |
| --- | --- | --- | --- |
| **1** | **验证 A1/A2**（`logcat | grep "Added WebView resources"` + 长按文字不再崩） | 5 分钟 | 确认 popup 修好 |
| **2** | **B1 加一行日志**（`WebViewWatch.schedule` 入口打 `LOGI`） | 10 分钟 | 确定是"没调用"还是"没日志" |
| **3** | **C1 `WebViewProbeActivity`**（受控测试） | 半天 | **一次性回答"file:// 谁在读"** |
| 4 | C2 查 `finish-imm:idle` 的调用者 | 半天 | 确认是否容器主动 finish |
| 5 | P1-3 WebView 代读 | 4–5 天 | **等 3 的结论后再决定** |

**核心判断**：
> **在 C1 有结论之前，任何"WebView 代读"的设计都是猜测** —— 这是本轮反复强调的教训。
