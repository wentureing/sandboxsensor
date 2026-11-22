# NEXTVM 两个功能缺陷：文字选择框卡死 / Via 主页显示源码

- 日期：2026-11-22
- 设备：`10AFA8169Z0039G`（vivo V2463A / Android 15）
- 容器：`com.nextvm.app`（`p15` / `660b8f5`）
- Guest：`mark.via_1795348656901.apk`（Via 浏览器）

> 说明：以下根因是基于框架机制的推断，**尚未有直接日志证据**（当前 logcat 里只有系统噪声）。
> 每节都给出「如何取到决定性证据」的操作，确认后再动手改。

---

## 缺陷 1：长按文字选择框卡死

### 现象

长按文本 → 系统浮动文本选择工具栏（复制/粘贴/全选那条）出现时卡死。

### 框架机制

```
TextView 长按
  → Editor.startSelectionActionMode()
    → EditorInfo / SelectionActionModeCallback
      → TextActionModeCallback.onCreateActionMode()
        → FloatingActionMode（Android 6+ 取代传统 ActionMode）
          → FloatingToolbar
            → PopupWindow.showAsDropDown()
              → WindowManager.addView(layoutParams with TYPE_APPLICATION_ATTACHED_DIALOG)
                → IWindowSession.addToDisplay(...)
```

关键点：**`FloatingActionMode` 是一个 `PopupWindow`，必须用宿主 Activity 的 window token 才能加窗**。

### 容器里的三个可能断点

| # | 断点 | 机制 | 可能性 |
| --- | --- | --- | --- |
| **A** | `WindowManager` 代理不完整 | guest 的 `Window` 实际是 stub Activity 的窗口；`PopupWindow` 需要 `WindowManagerImpl` → `IWindowSession`。若容器替换了 `WindowManager` 但没处理 `TYPE_APPLICATION_ATTACHED_DIALOG`，`addToDisplay` 会阻塞或返回无响应 | **高** |
| **B** | `FloatingToolbar` 的 `Menu` 回调被代理截断 | 容器若包装了 `Window.Callback`（`onCreatePanelMenu` / `onMenuItemSelected`），未转发 `FloatingToolbar` 的 menuitem 回调 → 菜单无法构建 → 主线程等待 | 中 |
| **C** | 主线程死锁 | 容器 native hook 里路径解析有全局锁（`g_patched_bases` 之类）；长按时会触发 `ClipboardManager` / `InputMethodManager` 的 binder 调用，Binder 线程回调主线程时与 hook 锁交叉 | 中 |

### 如何取到决定性证据

**复现时抓这三样**（复现后立刻执行）：

```powershell
# 1) 主线程栈（卡住时）
adb -s 10AFA8169Z0039G shell "kill -3 <pid>"     # 触发 ANR trace 落盘
adb -s 10AFA8169Z0039G shell "cat /data/anr/traces.txt" > anr.txt
# 或直接
adb -s 10AFA8169Z0039G shell "dumpsys activity processes" | Select-String nextvm

# 2) 窗口相关
adb -s 10AFA8169Z0039G shell "dumpsys window windows" | Select-String -Pattern "nextvm|Popup|Floating|Attached"

# 3) 复现窗口内的 logcat
adb -s 10AFA8169Z0039G logcat -d -b all -v threadtime > sel.txt
# 关注：WindowManager / ViewRootImpl / FloatingToolbar / InputMethod / Binder
```

**判据**：
- 栈里有 `FloatingToolbar` / `PopupWindow.showAsDropDown` / `WindowManagerImpl.addView` 且阻塞在 Binder → **断点 A**
- 栈里卡在容器自己的 native 函数（`libnextvm-native.so`）→ **断点 C**
- 窗口列表里看不到 popup 的 `Window{...}` 条目 → 断点 A 确认

### 可能的修复方向

| 方向 | 说明 | 难度 |
| --- | --- | --- |
| 补 `WindowManager` 代理的子窗口处理 | 确保 `TYPE_APPLICATION_ATTACHED_DIALOG`、`TYPE_POPUP` 用 stub Activity 的 token 透传 | 中 |
| 包装 `Window.Callback` 时完整转发 | `onCreatePanelMenu`/`onPreparePanel`/`onMenuItemSelected` 必须双向转发 | 低 |
| 检查 hook 全局锁粒度 | 把路径解析锁改细粒度或无锁（RCU/线程局部缓存） | 中 |
| 兜底：禁用浮动工具栏 | `textView.setCustomSelectionActionModeCallback` 替换为传统 `ActionMode`（非 popup） | 低（但影响体验） |

---

## 缺陷 2：Via 主页显示源代码

### 现象

Via 的默认主页显示为 HTML 源代码文本，新闻等网页基本正常。

### 为什么「新闻正常、主页异常」

这个对比很强：**网络加载的页面正常，只有主页异常** → 说明问题不在 WebView 渲染引擎，而在**主页的加载方式**。

Via 主页（`via://home` / 内置页）通常有两种实现：

| 实现 | 加载路径 | 容器的可能断点 |
| --- | --- | --- |
| A. APK 内 `assets/home.html` | `file:///android_asset/home.html` | **asset 路径被重定向后读不到/MIME 丢失** |
| B. 本地 `files/home.html` | `file:///data/.../files/home.html` | **路径翻译后 MIME 判断失败** |
| C. `data:text/html;base64,...` | 直接内存内容 | 一般不受影响（若异常说明 URL 被改写） |

**最可能：A 或 B —— `Content-Type` 丢失导致 WebView 按纯文本渲染。**

机制细节：
- WebView 对 `file://` 的 MIME 判定依赖 `URLConnection.guessContentTypeFromName()`（按扩展名）和 `AssetManager.openNonAsset()`
- 若容器的 `open`/`openat` hook 把 `/data/app/<容器>/base.apk!/assets/home.html` 重定向到沙箱路径，WebView 拿到的响应**可能丢失 MIME**，或读到的内容被 `WebResourceResponse` 包成了 `text/plain`
- 结果：HTML 被当纯文本显示（就是"显示源码"）

### 如何取到决定性证据

```powershell
# 1) 让用户打开 Via 主页，同时抓
adb -s 10AFA8169Z0039G logcat -d -b all -v threadtime > via.txt
# 关注：chromium / WebView / shouldInterceptRequest / android_asset / MimeTypeMap

# 2) 关键：看主页 URL 是什么
#    在 Via 地址栏长按复制，或
adb -s 10AFA8169Z0039G shell "dumpsys activity top" | Select-String -Pattern "mark.via|http|file|asset"

# 3) 容器沙箱里是否解出了 asset
adb -s 10AFA8169Z0039G shell "run-as com.nextvm.app find files/virtual -name '*.html' 2>/dev/null | head"
```

**判据**：
- 日志里有 `shouldInterceptRequest` 且返回 `WebResourceResponse` 的 mimeType 为空或 `text/plain` → **MIME 丢失确认**
- 沙箱里找不到 `home.html` → **asset 路径重定向失败确认**
- 主页 URL 是 `file:///android_asset/...` → 实现方式 A 确认

### 可能的修复方向

| 方向 | 说明 | 难度 |
| --- | --- | --- |
| 让 `assets` 路径**不参与**重定向 | assets 在 APK 内，通过 fd/mmap 读，本来就不该被路径 hook 拦截；加白名单跳过 `*.apk!/assets/` | **低** |
| 修复 `WebResourceResponse` 的 MIME | 若容器有自定义 WebView 拦截，补齐 `mimeType`/`encoding` | 低 |
| 检查 `AssetManager` 代理 | 容器若替换了 `AssetManager`，需保留 `openNonAsset` 的正确实现 | 中 |

---

## 两个缺陷的共同点（重要判断）

它们**都不是渲染问题**（p15 已经解决了 RenderThread 崩溃），而是**代理层不完整**：

| 缺陷 | 缺失的代理 |
| --- | --- |
| 文字选择框卡死 | `WindowManager` / `Window.Callback` 的子窗口与菜单回调 |
| Via 主页源码 | `AssetManager` / WebView 资源加载路径 |

这与之前 `P13`/`P16`（PackageManager 与 stat 代理）属于同一类问题：**容器的 hook 覆盖面够了，但代理实现不完整**。

---

## 建议的排查顺序

```
第 1 步：确认缺陷 2（成本低、判据明确）
  让用户打开 Via 主页 → 抓 logcat → 看有没 shouldInterceptRequest / mimeType
  同时 run-as 看沙箱里有没有 home.html

第 2 步：确认缺陷 1（需要复现）
  长按文本卡住时 kill -3 抓主线程栈 → 看阻塞点

第 3 步：按证据修，而不是按推断修
  两个缺陷都可能只需 1-2 处补丁，但必须先把断点定位准
```

## 待补的新探针（可选）

这两个缺陷暴露了探针的盲区：**它只测"能否读/能否拿到值"，不测"代理是否完整"**。可考虑增加：

| 新项 | 检测 | 归类 |
| --- | --- | --- |
| `P24` | `WindowManager` 能否添加 `TYPE_APPLICATION_ATTACHED_DIALOG` 窗口 | 新大类 `window` |
| `P25` | `AssetManager.open("home.html")` 是否可读、流长度 | 并入 `soload` 或新大类 `asset` |
| `P26` | `FloatingToolbar` 依赖的 `Editor` 是否可获取（反射 `TextView.getEditor`） | 并入 `window` |

---

## 七、实测证据（2026-11-22 20:00）

### 7.1 缺陷 2 的根因已定位：主页文件存在且可读

```bash
$ adb shell run-as com.nextvm.app find files/virtual -name '*.html'
files/virtual/data/mark.via_1795348656901/files/homepage2.html
```

**Via 的主页是本地的 `homepage2.html`**，它确实在沙箱里、路径翻译生效、文件可读。

所以问题**不是"读不到文件"**，而是**"读到了但被当纯文本渲染"** → 即 **MIME / 加载方式**问题。

| 已排除 | 依据 |
| --- | --- |
| 文件缺失 | `find` 能找到 |
| 路径翻译失败 | 就在 `files/virtual/data/<inst>/files/` 下 |
| WebView 引擎坏 | 新闻页面正常 |

**剩下两种可能**：

1. **Via 通过 `WebResourceResponse` 自己拦截并返回内容**，容器 hook 影响到了它的流读取，导致 `mimeType` 变 null
2. **Via 用 `content://` 或自定义 scheme 加载主页**，容器的 `ContentProvider`/`ContentResolver` 代理返回的 `InputStream` 没有伴随 MIME 信息

**下一步（必须做）**：让用户打开 Via 主页的同时抓 logcat，找 `shouldInterceptRequest` / `mimeType` / `homepage2.html` 相关行。

### 7.2 缺陷 1 的范围已收窄：窗口基础机制正常，问题在子窗口

```bash
$ adb shell dumpsys window windows | grep -E 'nextvm|mark.via'
Window #15 Window{5e6da9c u0 mark.via/mark.via.Shell type=1 }:
    package=mark.via
    mToken=ActivityRecord{1e31180 u0 com.nextvm.app/.stub.P0$Standard00 t1019 d0}
  Window #16 Window{9502f69 u0 com.nextvm.app/com.nextvm.app.MainActivity type=1 }:
```

**已确认正常的部分**：

| 项 | 状态 |
| --- | --- |
| guest Activity 跑在 stub `P0$Standard00` 里 | ✅ 符合设计 |
| guest 窗口能正常添加（`type=1` 应用窗口） | ✅ |
| guest 窗口 token 指向 stub ActivityRecord | ✅ |

**注意：窗口列表里只有 `type=1`（应用主窗口），没有 popup/子窗口。**

`FloatingToolbar` 用的是 `TYPE_APPLICATION_ATTACHED_DIALOG`（子窗口）。它没出现在列表里，说明**要么没加上，要么加上时卡住了** —— 这与"卡死"现象吻合。

**所以缺陷 1 的断点收窄到**：`WindowManager.addView` 对**子窗口类型**（popup/attached dialog）的处理。

**下一步（必须做）**：长按卡住时执行 `kill -3 <pid>` 抓主线程栈，看是否阻塞在 `WindowManagerImpl.addView` → `IWindowSession.addToDisplay`。

### 7.3 顺带确认：Via 在容器内是独立进程

```bash
$ adb shell ps -A | grep -E 'nextvm|via'
u0_a308  5615  ... S com.nextvm.app      ← 容器主进程
u0_a308  6127  ... S mark.via            ← guest 独立进程（共享 UID）
```

与 `P03`（同 UID 进程数=2）一致，符合当前容器设计。

