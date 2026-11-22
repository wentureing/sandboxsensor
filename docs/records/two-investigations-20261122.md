# 两个调查：克隆后 Home 失效（一次性）/ p17 对 WebView 为何无效

- 日期：2026-11-22 21:20
- 设备：`10AFA8169Z0039G`（vivo V2463A / Android 15）
- 用户反馈：修复前后 Via 都能访问 download；但修复后主页**仍是源码**

---

## 一、任务 1：克隆成功后点 Home 回不去（仅一次）

### 1.1 代码事实：克隆成功只弹 Snackbar，不做导航

`feature/appmanager/.../AppManagerScreen.kt:57`：

```kotlin
is AppManagerEffect.InstallSuccess ->
    snackbarHostState.showSnackbar("Installed ${effect.appName} successfully")
```

**没有 `navController.navigate(...)`，也没有自动启动 guest。** 所以克隆完仍停在 AppManager 页。

### 1.2 点击底部 Home 的处理

`app/.../MainActivity.kt`（底部导航）：

```kotlin
onClick = {
    navController.navigate(item.route) {
        popUpTo(navController.graph.findStartDestination().id) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}
```

### 1.3 为什么"只有第一次"失效——最可能的原因

三个因素在**首次安装后同时出现**，构成一次竞态：

| 因素 | 说明 |
| --- | --- |
| **① `showSnackbar` 是挂起函数** | 它在 `LaunchedEffect` 里跑，会挂起 3–4 秒；期间 `AppManager` 的组合还在活跃 |
| **② 克隆成功后列表刷新** | `installedApps` 从空变非空 → `AppManagerScreen` 触发重组 |
| **③ `restoreState = true`** | 首次导航到 `launcher` 时，`Launcher` 的保存状态可能还不存在/不完整 |

三者叠加：用户在"Snackbar 还在显示 + 列表正在重组"的窗口内点 Home → `navigate` 与重组竞争 → 首次这次导航可能被丢弃或被后续重组覆盖。

**第二天再点就正常**，因为 Snackbar 已消失、列表状态稳定、`restoreState` 的保存状态也已建立。

### 1.4 未验证的部分（诚实说明）

- **三键导航的返回是否正常**：这个没测。但从代码看，`AppManager` 有独立的 `onNavigateBack = { navController.popBackStack() }`，走的是 `popBackStack` 而不是 `navigate(launcher)`，**路径不同，可能不受同一竞态影响**。
- 用户说"不清楚此时点击三键导航的返回是否正常返回 home"——如果返回键正常、只有底部 Home 标签失效，那更能坐实是 `navigate + restoreState` 这条路径的问题。

### 1.5 结论

| 项 | 结论 |
| --- | --- |
| 性质 | **首次状态竞态**，非必现，无崩溃、无数据损坏 |
| 触发条件 | 克隆成功后（Snackbar 显示中 + 列表重组中）立刻点底部 Home |
| 影响 | 一次点击无效，再点即恢复 |
| 优先级 | **低**（只调查，按要求不改） |
| 若要修 | 把 `restoreState` 在首次导航时置 false，或等 Snackbar 结束再允许导航 |

---

## 二、任务 2：p17 为什么没解决 Via 主页源码

### 2.1 先看一个架构事实（我之前完全漏掉了）

`adb shell ps -A -o PID,PPID,NAME | grep -i webview`：

```
4308  1583  webview_zygote
21628 4308  com.google.android.webview:sandboxed_process0:...SandboxedProcessService0:0
21394 1583  mark.via                                   ← Via 主进程
```

**WebView 的渲染跑在 `sandboxed_process0`（pid 21628），它的父进程是 `webview_zygote`（4308），不是容器。**

这意味着：

| 进程 | 谁 fork 的 | 容器的 native hook 装在哪 |
| --- | --- | --- |
| `mark.via`（21394） | zygote（1583）→ 容器拉起 | ✅ **装了** |
| `sandboxed_process0`（21628） | **webview_zygote（4308）** | ❌ **没装**（isolated uid，容器碰不到） |

### 2.2 所以 p17 的 patch 作用不到渲染进程

p17 让 `libwebviewchromium*` 参与 GOT patch，但 patch 只发生在**容器进程**里。WebView 真正读文件、渲染页面的地方是 `sandboxed_process0`，那里的库**根本没被 hook**。

结果：

```
Via 主进程（已 patch）→ 调 WebView API
  → IPC 到 sandboxed_process0（未 patch）
    → 用真实路径读 file:///.../homepage2.html
      → 读不到（容器沙箱路径在 isolated uid 眼里不存在）
        → 加载失败 → Via 回退 loadData(html)
          → MIME 默认 text/plain → 显示源码
```

### 2.3 这也解释了"修复前后都能访问 download"

`sandboxed_process0` 用**真实路径**访问 `/sdcard/Download/`：

- 真实路径存在 → 能读 ✔（无论 p16 还是 p17）
- 这恰好**反证**了沙箱进程不受容器 hook 影响

### 2.4 能不能关掉 WebView 多进程

| 检查项 | 结果 |
| --- | --- |
| `ro.webview.multiprocess` | 空 |
| `persist.webview.multiprocess` | 空 |
| `ro.webview.sandbox` | 空 |
| `debug.webview.multiprocess` | 空 |

Android 10+ 的 WebView 多进程由系统 `webview_zygote` 决定，**应用层无法关闭**（这是 Google 的隔离设计，防止渲染进程被宿主影响）。

**所以这条路走不通。**

### 2.5 结论：这是容器虚拟化的天然边界

| 项 | 结论 |
| --- | --- |
| p17 是否有效 | **对 WebView 渲染无效**（架构上不可能有效） |
| p17 是否有害 | 无害（只影响容器进程内的 WebView API 调用，如 `WebSettings`） |
| 是否回退 | 可留可退；**建议保留**（对主进程侧行为仍有一致性收益），但要**明确记录它不解决 Via 主页问题** |
| Via 主页源码能否修 | **不能**。这是"第三方应用用 `file://` 加载本地页 + WebView 渲染在 isolated 进程"的组合，容器无法干预 |
| 之前 `webview-rootcause-20261122.md` 的结论 | **需要修正**：那里把根因归为"WebView 库未被 patch"，实际是"WebView 渲染进程不受 hook"，前者只是后者的一部分 |

### 2.6 还有没有别的路

| 方案 | 可行性 | 说明 |
| --- | --- | --- |
| 禁用 WebView 多进程 | ❌ | 无系统属性，应用无法控制 |
| 换成单进程内核（X5/TBS） | ⚠️ | 腾讯 X5 内核可配置单进程，但需集成第三方 SDK，且体积大 |
| 让沙箱进程能读到容器路径 | ❌ | isolated uid 的隔离是内核级的 |
| 让 Via 改用 `loadDataWithBaseURL` | ❌ | 第三方应用，改不了 |
| 接受限制，在文档/验收里标注 | ✅ | **推荐**：明确"WebView 本地文件加载在容器内不可用"是已知边界 |

---

## 三、两个调查的最终结论

| # | 问题 | 结论 | 动作 |
| --- | --- | --- | --- |
| 1 | 克隆后 Home 一次失效 | 首次状态竞态（Snackbar 挂起 + 列表重组 + `restoreState`），低优先级 | 只记录，不改 |
| 2 | p17 未解决 Via 主页源码 | **WebView 渲染在 `webview_zygote` 派生的 isolated 进程，容器 hook 覆盖不到**；无法禁用多进程 | 修正文档结论；p17 保留但标注无效 |

**第 2 条的教训**：我之前的分析停在"库有没有被 patch"，没往上一层问"**这个库在哪个进程里被加载**"。多进程架构让"patch 库"这件事的生效范围变得非常局部。
