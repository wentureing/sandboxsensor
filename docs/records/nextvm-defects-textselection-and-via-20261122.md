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

---

## 八、自动化调查结果（2026-11-22 20:00–20:11）

### 8.1 缺陷 2 复现成功（截图 + 源码）

用 `uiautomator dump` 定位到 Via 卡片 `bounds=[702,620][889,807]`，`input tap 795 713` 启动。

**截图确认**：Via 页面显示的是完整的 HTML 源码文本（`<!DOCTYPE html>...`），标题栏显示 `homepage2.html`。

**源码里的关键线索**：

```html
<link rel="stylesheet"
  href="/data/user/0/com.nextvm.app/files/virtual/data/mark.via_1795348656901/files/homepage.css">
```

→ Via 把 `getFilesDir()` 的结果**嵌进了 HTML**，而容器内它就是沙箱路径。**这本身正常**（容器内 `getFilesDir()` 返回沙箱路径是正确的）。

### 8.2 缺陷 2 的高置信根因：WebView 的库被跳过 patch

logcat 反复出现（每个 guest 进程都一次）：

```
I NextVM-Native: GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0):
                 /system/lib64/libwebviewchromium_plat_support.so
I NextVM-Native: GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0):
                 /system/lib64/libwebviewchromium_loader.so
```

**这两条是 p13/p15 引入的 `NS_HOOK_SYSTEM_LIBS=0` 策略的直接结果**。

`libwebviewchromium*.so` 是 WebView 读本地资源（HTML/CSS/JS）的必经之路。它没有被 patch，意味着：
- WebView 内部的文件访问**不经过容器的路径翻译**
- 如果 Via 让它读沙箱路径 `/data/user/0/com.nextvm.app/files/virtual/...`，WebView 的沙箱进程（isolated uid）**可能读不到**
- 于是内容/MIME 处理异常 → 显示源码

**待确认的一步**（价值最高）：

```powershell
# 让 Via 打开主页的同时抓，找 shouldInterceptRequest / WebResourceResponse / mimeType
adb -s 10AFA8169Z0039G logcat -d -b all -v threadtime > via-home.txt
```

若日志里出现 `WebResourceResponse` 的 `mimeType` 为空/null/`text/plain` → 即缺陷 2 的最终定位。

### 8.3 缺陷 1 不是"卡死"，是 **Input dispatch ANR**（8 次）

`dumpsys activity lastanr` 给出的原因：

```
Reason: Input dispatching timed out
(Window:3d8754b type=1 mark.via/mark.via.Shell (server) is not responding.
 Waited 5000ms for MotionEvent).
```

logcat events 里的完整记录（**8 次，19:57 → 20:10**）：

```
19:57:53  am_anr  [0,2754,com.nextvm.app:p0,...,Input dispatching timed out (Window:..., mark.via/mark.via.Shell ...)]
19:58:25  am_anr  [0,2754,...]
19:58:42  am_anr  [0,4153,...]
20:03:26  am_anr  [0,7041,...]
20:05:17  am_anr  [0,9383,...]
20:08:34  am_anr  [0,11075,...]
20:10:02  am_anr  [0,11168,...]   ← 我自动化长按触发的
20:10:13  am_kill [0,11168,...,user request after error]
```

**含义**：输入事件已投递到 Via 窗口，但 guest 主线程 **5000ms 内没有消费**，系统判定 ANR。

**排除了什么**：

| 假设 | 证据 | 结论 |
| --- | --- | --- |
| 窗口加不上 | `dumpsys window` 里窗口 `type=1` 存在、token 正确 | ❌ 排除 |
| 容器 hook 全局锁 | `sc_v91_snapshot` 默认不执行（`g_sc_maps_fd=-1`）；`g_sc_arm_mutex` 只在初始化用一次；`g_mutex` 在 JNI 低频路径 | ❌ 排除这几处 |
| 拿到主线程栈 | `bugreport` 里 8 份 `anr_*` 全部**没有 p0 进程段**（进程在 ANR 后被 kill，debuggerd 没 dump 到） | ⚠️ 拿不到 |

**剩下的最可能解释**：**主线程被 WebView 的渲染/加载任务占满**——也就是**缺陷 2 的连带后果**。Via 主页渲染异常（源码模式）→ WebView 反复布局/重排 → 主线程没有余力处理输入事件 → 5 秒超时。

这条推断的解释力很强：
- 为什么**只有 Via** 频繁 ANR（WebView 是最重的 guest）
- 为什么**长按**容易触发（长按需要 WebView 做 hit-test + 文本分析，正是主线程最忙的时候）
- 为什么"新闻页基本正常"（网络页面能正常渲染，主线程不忙）

### 8.4 结论：两个缺陷很可能同源

```
WebView 的库未 patch（NS_HOOK_SYSTEM_LIBS=0）
  → Via 主页（本地 HTML）加载异常 → 显示源码            [缺陷 2]
  → WebView 反复重试/重排 → 主线程持续繁忙
  → 输入事件 5 秒未被消费 → Input dispatch ANR          [缺陷 1]
```

### 8.5 建议的修复顺序

| # | 动作 | 说明 | 风险 |
| --- | --- | --- | --- |
| **1** | 把 `libwebviewchromium*.so` 加入 patch 白名单（即使 `NS_HOOK_SYSTEM_LIBS=0`） | 让 WebView 的文件访问走容器翻译 | 中（WebView 库大，patch 可能引入新 bug） |
| 2 | 或反向：确认 Via 的加载方式（反编译 Via，看 `loadDataWithBaseURL` 的 mimeType 参数） | 若是 Via 自身 bug，容器无需改 | 低 |
| 3 | 修完后再测长按 | 若 ANR 消失，证明同源 | — |
| 4 | 若 ANR 仍在：`am hang` / 降低 ANR 阈值来抓栈 | 需要能拿到 p0 栈 | 中 |

**关键提醒**：改动 1 会**推翻 p13/p15 的 `NS_HOOK_SYSTEM_LIBS=0` 保守策略**。建议做成**精确白名单**（只放行 `libwebviewchromium*`），而不是把 `NS_HOOK_SYSTEM_LIBS` 整体打开。

---

## 九、修正：缺陷 1 是**长按特有**，与缺陷 2 **独立**（2026-11-22 20:19–20:23）

### 9.1 关键对照实验

| 操作 | 进程是否存活 | 是否新增 ANR |
| --- | --- | --- |
| **普通点击** `input tap 400 800` | ✅ 存活（pid 20772） | ❌ 无 |
| **滑动滚动** `input swipe 540 1600 540 800 400` | ✅ 存活 | ❌ 无 |
| **长按** `input motionevent DOWN 300 500` +1200ms `UP` | ❌ **被杀** | ✅ **每次必现** |

→ **只有长按触发**。普通点击与滚动完全正常。

**这推翻了 §8.4「两个缺陷同源」的推断**：缺陷 1 与 WebView 的 MIME 问题**无关**，是长按路径特有的。

### 9.2 完整生命周期时间线（一次长按实验）

```
20:21:57.852  VAMS: Started activity: mark.via.Shell → com.nextvm.app.stub.P0$Standard03
20:21:58.495  wm_on_create_called:  mark.via.Shell
20:21:58.543  wm_on_resume_called:  mark.via.Shell
20:21:58.594  input_focus: Focus request Window mark.via/mark.via.Shell
20:21:58.631  input_focus: Focus entering mark.via/mark.via.Shell
20:22:17.389  input_interaction: [Gesture Monitor]（此前的正常交互）
   ...        ← 长按发生在这里
20:22:58.703  am_anr: Input dispatching timed out (mark.via/mark.via.Shell)
20:23:09.518  wm_finish_activity: [stub P0$Standard03] **user-terminated**
20:23:09.546  input_focus: Focus leaving ... reason=NO_WINDOW
20:23:09.565  wm_on_resume_called: com.nextvm.app.MainActivity   ← 退回容器
```

**要点**：
- Via 的窗口从创建到 ANR 前**一直存在**，焦点也正常
- ANR 后 **10.8 秒**固定被终止，原因 `user-terminated` / `am_kill ... user request after error`
- **这是 vivo ROM 的自动行为**（ANR 对话框超时后自动关闭），不是用户点的

### 9.3 为什么拿不到主线程栈（已确认是硬限制）

```
$ adb shell run-as com.nextvm.app kill -0 20772
kill: bad pid '20772': Permission denied          ← SELinux: runas_app 域

$ adb shell kill -0 20772
/system/bin/sh: kill: 20772: Operation not permitted   ← 不同 uid
```

- `run-as` 切到的是 **`u:r:runas_app:s0`** 域，**无权向 `u:r:untrusted_app` 域（guest 进程）发信号**
- `bugreport` 里的 8 份 `anr_*` **全部没有 p0 进程段**——`debuggerd` dump 时同样被挡
- `/data/anr/anr_*` 是 `system:system 0600`，shell 读不了

**结论：在这台 vivo 上，主线程 Java 栈拿不到。** 只能靠 `dumpsys activity lastanr` 的 Reason 字段 + 行为对照实验来定位。

### 9.4 现在的确定结论

| 项 | 结论 | 证据 |
| --- | --- | --- |
| 触发条件 | **仅长按** | 点击/滑动正常，长按必 ANR |
| 表现 | ANR 5 秒 → 10.8 秒后被系统终止 | `am_anr` + `am_kill user request after error` |
| 窗口状态 | 正常（创建、焦点、存活） | `wm_on_*` + `input_focus` |
| 缺陷 2 关系 | **独立** | 超时与 MIME 无关 |
| 主线程栈 | 拿不到 | SELinux `runas_app` 限制 |

### 9.5 剩下的最可能原因（按可能性排序）

长按在 Android 里会走一条**普通点击不会走**的路径：

```
MotionEvent.ACTION_DOWN (长按超时)
  → View.performLongClick()
    → WebView 的 LongClick 处理（AwContents）
      → 文本选择：startSelectionActionMode
        → Editor / TextActionModeCallback
          → FloatingActionMode  ← 一个 PopupWindow
            → WindowManager.addView(TYPE_APPLICATION_ATTACHED_DIALOG)
```

| # | 可能断点 | 为什么可能 |
| --- | --- | --- |
| **A** | `FloatingActionMode` 的 popup 窗口加不上（容器 WindowManager 代理不处理子窗口类型） | 与之前 `dumpsys window` 里从未出现过 popup 一致 |
| **B** | 长按触发的 Binder 回调（`IActivityManager` / `IWindowManager`）在容器代理层阻塞 | 容器替换了这两个服务 |
| **C** | WebView 的 `HandleView`（选择手柄）需要额外 Surface | 未见 popup 窗口出现 |

### 9.6 下一步（需要更早抓窗口）

**我上次抓窗口的时机错了**：长按后 2 秒抓，实际那时窗口已被 finish（20:23:09）。

正确时机：**长按后 0.5–1 秒**（ANR 前）：

```powershell
adb shell "input motionevent DOWN 300 500"; Start-Sleep -Milliseconds 1200
adb shell "input motionevent UP 300 500"
Start-Sleep -Milliseconds 500
adb -s 10AFA8169Z0039G shell "dumpsys window windows" > win-early.txt
# 找：有没有 type=2/3/1000-1003/2008 的窗口出现并卡住
```

**判据**：
- 若出现 `TYPE_APPLICATION_ATTACHED_DIALOG` 窗口但状态异常 → **断点 A**
- 若完全没有新窗口 → 长按处理在更早的环节就卡住（B/C）

### 9.7 修复方向的初步判断

| 若确认是断点 A | 修 `WindowManager` 代理，让 `TYPE_APPLICATION_ATTACHED_DIALOG` 用 stub Activity 的 token 正常透传 |
| --- | --- |
| 若确认是断点 B | 检查容器的 `IActivityManager`/`IWindowManager` 代理是否有同步 Binder 调用阻塞主线程 |
| 兜底方案 | 给 guest 的 TextView/WebView 设置自定义 `ActionMode.Callback`，改用**非 popup** 的传统 ActionMode（牺牲体验换稳定） |



