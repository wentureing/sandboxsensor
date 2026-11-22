# 结论修正：文字选择在容器内完全正常，问题在 WebView（Via）

- 日期：2026-11-22 20:50
- 设备：`10AFA8169Z0039G`
- 容器：`f038c03`（p16，含 CmdReceiver + LAUNCH_ACTIVITY）
- 探针：v1.6（`3be36b2`，含 `UiTestActivity`）

---

## 一、决定性实验：容器内 TextView 的选择菜单完全正常

用 `LAUNCH_ACTIVITY` 把 `UiTestActivity` 起在容器内的 guest 进程里：

```bash
adb shell am broadcast -n com.nextvm.app/.CmdReceiver \
    -a com.nextvm.cmd.LAUNCH_ACTIVITY \
    --es pkg com.sandbox.probe \
    --es activity com.sandbox.probe.UiTestActivity
```

然后长按文字区 `(400,600)` 1.3 秒，截图（`probe-reports/M-1.png`）：

**结果**：

| 项 | 状态 |
| --- | --- |
| 选中高亮（蓝色，"这里"） | ✅ 出现 |
| 选择手柄（两个蓝色水滴） | ✅ 出现 |
| **浮动菜单** | ✅ **复制 / 全选 / 搜索 / 分享 全部显示** |
| 进程是否存活 | ✅ 存活（pid 29602，未 ANR） |

→ **容器的 `FloatingActionMode`（PopupWindow）链路完全正常。**

## 二、因此，之前几条推断全部作废

| 之前的推断 | 状态 | 依据 |
| --- | --- | --- |
| 「容器 `WindowManager` 子窗口处理不完整」 | ❌ **作废** | popup 正常显示，菜单项齐全 |
| 「`Window.Callback` 未转发 `onCreateActionMode`，menu 为空」 | ❌ **作废** | 菜单项都在 |
| 「缺陷 1（长按卡死）与缺陷 2（Via 主页源码）独立」 | ❌ **作废** | 两者都只在 Via/WebView 出现，容器通用路径正常 |
| 「`WMS` 里 popup 存在但看不见 → 空壳」 | ⚠️ 部分成立，但只针对 Via | Via 里确实不可见，但那是 WebView 的问题 |

## 三、修正后的结论：两个缺陷同源，都在 WebView

| 缺陷 | 表现 | 仅 Via 出现？ |
| --- | --- | --- |
| 长按文字无法交互 → 几秒后 ANR | 选中区域出现但菜单不可用 | ✅ 是（容器 TextView 正常） |
| 主页显示 HTML 源码 | `homepage2.html` 当纯文本渲染 | ✅ 是（网络页正常） |

**共同触发条件：WebView 在容器内运行。**

## 四、根因指向同一处：WebView 的 native 库未被 patch

logcat 反复出现（每个 guest 进程都一次）：

```
I NextVM-Native: GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0):
                 /system/lib64/libwebviewchromium_plat_support.so
I NextVM-Native: GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0):
                 /system/lib64/libwebviewchromium_loader.so
```

**这两条是 p13/p15 引入的 `NS_HOOK_SYSTEM_LIBS=0` 策略的直接结果。**

`libwebviewchromium*` 是 WebView 读本地资源、访问文件的必经之路。它没被 patch，意味着：

| 场景 | 容器翻译是否生效 | 结果 |
| --- | --- | --- |
| WebView 读本地 HTML（Via 主页） | ❌ 不生效 | 路径/URI 解析异常 → 内容被当纯文本 → **显示源码** |
| WebView 长按处理（选择/渲染） | ❌ 不生效 | 内部资源加载失败 → 主线程阻塞 → **ANR** |
| WebView 加载网络页 | 不需要翻译 | 正常 |
| 普通 `TextView` 选择 | 走应用自己的库（已 patch） | **正常** |

**这条链路解释了所有已知现象**，包括为什么只有 Via 出问题、为什么新闻页正常。

## 五、修复方向

### 首选：精确白名单放行 `libwebviewchromium*`

在 `native-hook.cpp` 的过滤链里，把 WebView 栈从"系统库（默认不 patch）"提升为"强制 patch"：

```cpp
// 3.5) WebView 栈：必须 patch，否则其文件/资源访问不经容器翻译
const bool is_webview = strstr(lib_name, "libwebviewchromium") != nullptr;
if (is_webview) {
    // fallthrough: 继续 patch
} else {
    // 4) 其余系统库：由 NS_HOOK_SYSTEM_LIBS 控制
}
```

**注意**：这是**精确白名单**（只放行 `libwebviewchromium*`），不是把 `NS_HOOK_SYSTEM_LIBS` 整体打开——否则会重演 GPU 驱动被污染的老问题。

### 风险与回退

| 风险 | 说明 | 回退 |
| --- | --- | --- |
| patch WebView 库可能引入新崩溃 | 与 GPU 驱动类似，patch 不理解的大库有风险 | 加开关（编译期宏或运行时属性），出问题可关闭 |
| WebView 有独立的沙箱进程 | `com.google.android.webview:sandboxed_process0`，patch 主进程的 WebView 库是否覆盖它需要验证 | 观察 `GOT patch skipped` 日志是否出现在沙箱进程 |

### 验证方法（修完后）

```bash
# 1) 确认 WebView 库被 patch（不再出现 skipped）
adb logcat -d -b all | Select-String "libwebviewchromium"

# 2) Via 主页应正常渲染（不再显示源码）
# 3) Via 里长按文字应出现 复制/全选 菜单且可交互
# 4) 不应再有 Input dispatching timed out (mark.via/mark.via.Shell)
```

---

## 六、教训

这次排查绕了很多弯路，核心教训：

1. **"现象相同"不等于"病因相同"**。长按 ANR 在 Via 和一次容器测试里都出现，我把它归因为容器通用问题；但真正区分变量（换成纯 `TextView`）后，才看出是 WebView 特有。
2. **先做最小对照实验，再深入机制**。如果一开始就在容器里放一个 `TextView` 测长按，能省掉后面全部关于 `Window.Callback`/`PopupWindow` 的推断。
3. **`UiTestActivity` 这类"最小可复现页面"的价值极高** —— 它把 Via/WebView 的变量彻底排除掉了。
