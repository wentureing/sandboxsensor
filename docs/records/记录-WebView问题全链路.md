# 记录-WebView 问题全链路

- 更新：2026-11-23
- 合并自：`webview-rootcause`、`webview-hook-exposure`、`webview-proxy-direction`、`webview-support-assessment`、`host-read-stream-pattern`、`two-investigations`（WebView 部分）

---

> ## ⚠️ 更正（2026-11-24 18:50，device）
>
> 本文第二节的根因「**WebView 渲染在 isolated 进程，容器 hook 覆盖不到 → 读不到沙箱**」
> **已被 W0–W7 实测推翻**：
>
> - **W2**：loadUrl("file://<沙箱>/wvprobe.html") **能读到文件**（body 里是完整 HTML 源码），
>   只是被当 	ext/plain 显示。**如果 renderer 读不到，body 应该是 EMPTY 或报错。**
> - **W1**：loadDataWithBaseURL 渲染完全正常 → renderer 本身没问题。
>
> **所以"读不到"是错的，真因是 MIME 判定**。当前权威计划见 docs/计划-WebView修复.md。
>
> 本文**仍然有效的部分**：第三节「三次错误的排查方向（教训）」、第五节「宿主代读 + stream」的机制分析。
> **已作废的部分**：第二、六、七节里所有基于「renderer 读不到」的推论。

---

## 一、现象

| # | 现象 | 范围 |
| --- | --- | --- |
| 1 | **Via 主页显示 HTML 源码**（不是渲染后的页面） | 只在容器内；容器外正常 |
| 2 | **Via 里长按文字 → 选中区域出现但菜单不可交互 → 几秒后 ANR** | 只在容器内 |
| 3 | 网络页面正常 | 容器内外一致 |

**关键对照实验（决定性）**：在容器内跑 `UiTestActivity`（纯 `TextView`），长按后
**选中高亮 + 两个选择手柄 + 浮动菜单「复制/全选/搜索/分享」全部正常**。

→ **容器的 `FloatingActionMode` / `PopupWindow` / `WindowManager` 链路没问题。**
→ 问题**只在 WebView**，不在容器通用路径。

---

## 二、根因：WebView 渲染在 isolated 进程，容器 hook 覆盖不到

### 2.1 进程架构（实测）

```
4308  1583  webview_zygote
21628 4308  com.google.android.webview:sandboxed_process0:...SandboxedProcessService0:0
21394 1583  mark.via                              ← 容器 hook 只装在这里
```

**`sandboxed_process0` 的父进程是 `webview_zygote`，不是容器。** 它是 isolated uid，
容器**无法**给它安装 hook。

### 2.2 完整失败链

```
Via 主进程（容器 hook 覆盖范围）
  → 调 WebView API
    → IPC 到 sandboxed_process0（容器碰不到）
      → 用真实路径读 file://.../homepage2.html
        → 读不到（容器沙箱路径在 isolated uid 眼里不存在）
          → onReceivedError → Via 回退 loadData(html)
            → MIME 默认 text/plain → 显示源码
```

**这也解释了"修复前后 Via 都能访问 `/sdcard/Download`"**：
renderer 用**真实路径**访问真实存在的 `/sdcard/Download`，所以能读；
而容器沙箱在 `/data/user/0/<容器>/` 下，它读不到。

---

## 三、三次错误的排查方向（教训）

| 错误方向 | 为什么错 | 纠正它的事实 |
| --- | --- | --- |
| ① 归因"容器的 `WindowManager` 子窗口处理不完整" | 长按 ANR 看起来像窗口问题 | 容器内纯 `TextView` 长按**完全正常** |
| ② 归因"`libwebviewchromium` 未被 patch" | 日志确实有 `GOT patch skipped` | **patch 了也没用**：渲染在 isolated 进程，patch 主进程的库无效 |
| ③ 归因"沙箱该放外部存储（Gaia 那样）" | Gaia 的目录名看起来在 `/sdcard/Android/data/...` | 探针实测 Gaia 沙箱在 `/data/user/0/<容器>/gaia/`（**内部**） |

**共同教训**：
- **"现象相同" ≠ "病因相同"**（长按 ANR 在 Via 和别的场景都出现，但病因只在 WebView）
- **先做最小对照实验，再深入机制**（一个 `TextView` 就能排除整条窗口链路）
- **"库有没有被 patch"之上还有一层问题："这个库在哪个进程里加载"**

---

## 四、p17 的尝试与回退

**p17 的内容**：在 native hook 过滤链里把 `libwebviewchromium*` 从"系统库默认跳过"提升为"始终 patch"。

**结果**：
- 架构上不可能有效（渲染在 isolated 进程）
- 无害，但也没用
- **已回退**（`a528e09`），保存在分支 `backup-webview-hook`

**保留的理由**：对容器进程内的 WebView API 调用（`WebSettings` 之类）仍有一致性收益。
**回退的理由**：避免后来者误以为它是那个问题的修复。

---

## 五、"宿主代读 + stream 跨进程"：Gaia 可能的机制

### 5.1 模式

```
guest 的 WebView 请求 file:/
  → 宿主（有权限）在 guest 进程里拦截
    → 宿主按容器路径规则打开真实文件
      → 用 WebResourceResponse 把 stream 交给 WebView
```

**为什么能绕过 isolated uid 限制**：**fd/stream 是跨进程传递的，不需要接收方有路径权限**。
（同一个原理在"容器 UI 能导入 APK、命令行不能"上已经出现过：SAF 返回 fd，不走路径解析。）

### 5.2 隐蔽性（三层视角）

| 视角 | 能否检测 | 依据 |
| --- | --- | --- |
| **WebView 内部（JS）** | **几乎不能** | `location.href` 仍是 `file:/`；同源/CSP 一致；`performance.getEntries` 的 `transferSize` 在 `file:/` 下本来就是 0 |
| **guest 的 Java 代码** | **能** | ① 响应头缺 `Content-Length`/`Last-Modified` 时 `fetch().headers` 能看出；② 若 guest 自己设过 `WebViewClient`，行为差异可察觉 |
| **系统/native** | 能看到 | 宿主自己的 `/proc/self/fd` 会暴露真实路径 |

**最容易被抓的破绽：响应头不完整。**

### 5.3 还能用在哪

模式本质：**跨 UID 边界时，fd 是唯一不需要对方有路径权限的载体**。

| 价值 | 场景 |
| --- | --- |
| **高** | WebView 的 `file:/`、`content:/`、`assets/`、`Blob`（全是 isolated 进程） |
| 中 | `BitmapFactory.decodeFile` → `decodeFileDescriptor`；`MediaPlayer.setDataSource(path)` → `setDataSource(fd)`；ExoPlayer 的 `ContentDataSource` |
| 低 | guest 主进程的普通 `open`（容器已用路径翻译解决） |

### 5.4 心智压力（这是最该权衡的）

| 项 | 难度 |
| --- | --- |
| **hook `WebViewClient`**（framework 方法） | **高** |
| MIME / 编码 / 相对路径 / range 四件套 | 中（任一漏掉就是白屏或源码） |
| 大文件用 `ByteArray` | OOM |
| 同步 IO 在 `shouldInterceptRequest` | 卡死 WebView |
| **黑盒调试** | **高** |

**另有一项容易被忽略的责任**：宿主代读意味着**替 guest 打开了它本不该打开的文件**，
必须自己实现路径白名单（→ `HostReadPolicy`），否则等于给 guest 开后门。

---

## 六、当前已知边界（不再投入的方向）

| 方向 | 结论 |
| --- | --- |
| patch `libwebviewchromium` | ❌ 无效（渲染在 isolated 进程） |
| 在容器内自建进程复刻 WebView | ❌ 不可行（renderer 由系统 `webview_zygote` fork，应用层无入口） |
| 禁用 WebView 多进程 | ❌ 不可行（`ro.webview.multiprocess` 等属性均为空，Android 10+ 应用无法控制） |
| 换 X5/TBS 内核 | ❌ 被 framework 类问题堵死（X5 提供的是 `com.tencent.smtt.sdk.WebView`，guest 不会用） |
| 沙箱迁到外部存储 | ❌ 前提被推翻（Gaia 在内部存储却做到了） |
| **宿主代读（`WebResourceResponse`）** | ⚠️ **唯一与证据相符的方向**，但需要先找到可行的注入点（见 `计划/README.md` P1-3） |

---

## 七、待验证的前提（P1-3 之前必须做）

探针 v1.8：把最小 HTML 写进自己的 `getFilesDir()`（落在容器沙箱内），输出绝对路径，
再在 Via 里打开那个路径。

| 结果 | 结论 |
| --- | --- |
| 正常渲染 | renderer 能读内部存储 → 方向要改 |
| **显示源码/失败** | **证实需要宿主代读** |

**为什么这个前提重要**：如果连"容器沙箱内的文件"都能被 renderer 读到，
那 Via 主页源码就不是权限问题，而是别的原因（如 MIME 传递）。
