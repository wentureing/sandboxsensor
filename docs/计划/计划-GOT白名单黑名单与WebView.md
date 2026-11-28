# 计划：GOT patch 作用域的「黑名单 / 白名单」（包 × 库），先对 via 的 WebView 开（2026-11-28）

- 背景：`W-ext-synthpath` 实测 `EMPTY` ⇒ **Chromium（`libwebviewchromium.so`）内部 libc 调用不在任何翻译模块覆盖内**。
- 用户要求（2026-11-28）：把"系统库"的隐性推定改成**显式两张表**——
  **黑名单 = 不拦截**；**白名单 = 即使命中黑名单仍然拦截**；两者各分**包**与**库**两层；**先对 via 包的 WebView 相关库开启**。
- 关联：`主题/册-路径沙箱与重定向.md` §四之二/四之四、`主题/册-WebView与file-MIME.md`、
  `docs/代码镜像/core/hook/cpp/core/lib_filter.md`、`变更/20261127-N2a-lib-filter.md`

---

## 一、现状调查："系统库"的定义从哪来（这是要改的地方）

**结论：现在没有显式清单，"系统库"是"推定"出来的**，来源有三处：

| # | 来源 | 内容 | 位置 |
| --- | --- | --- | --- |
| 1 | **编译期宏** `NS_HOOK_SYSTEM_LIBS`（默认 **0**） | 唯一"系统库总开关"：0 = 不 patch 系统库 | `native-hook.cpp` 的 `patch_library_got` |
| 2 | **`classify_lib` 四级判据**（N2-A 抽出的表） | ① 容器自身（含 `com.nextvm.app`/`libnextvm`）② 图形栈（六条关键词）③ app 库（含 `/data/app/`、`/data/user/`、`/virtual/`）④ **其余 → `system_lib`** | `core/lib_filter.cpp` |
| 3 | **callback 层早期过滤** | `libnextvm` / `linker` / `vdso` / `[vdso]` / `libart.so` / `libhwbinder` + CM `libDexHelper` 特例（mmap-only） | `install_hooks_callback` |

**所以"系统库" = 不匹配 app 路径、又不是 self/graphics 的一切库** —— 包含 `/system`、`/system_ext`、`/apex`、**`/product`**、`/vendor`，以及**没有路径的库名**（如 `libc.so`）。

**实证**（本机）：WebView 包是 `com.google.android.webview`，APK 在 **`/product/app/WebViewGoogle64/WebViewGoogle64.apk`**
⇒ `libwebviewchromium.so` 的路径形如 `/product/app/…!/lib/arm64-v8a/libwebviewchromium.so` ⇒ **不匹配 `/data/app/`** ⇒ `system_lib` ⇒ **被 skip**。
这就是"Chromium 的 `open` 不被翻译"的机制原因。

> 现方案的**问题**：想 patch 某一个系统库（WebView）时，只有"打开 `NS_HOOK_SYSTEM_LIBS=1`"这一个旋钮 —— 那是**全局放大**，历史两次崩溃的入口。所以必须改成显式白名单。

---

## 二、设计：四张表（包 × 库）+ 优先级

```cpp
struct LibFilterConfig {
    // 包层：限定"哪些 guest 才应用库白名单" / 排查时整包禁掉
    std::unordered_set<std::string> pkg_allow;  // 例：{"mark.via"}
    std::unordered_set<std::string> pkg_deny;   // 例：{}（排查用）
    // 库层：子串匹配（与现有 graphics 判据同风格）
    std::unordered_set<std::string> lib_allow;  // 例：{"libwebviewchromium", "libwebviewchromium_plat_support", "libmonochrome"}
    std::unordered_set<std::string> lib_deny;   // 例：{"libGLES","libEGL","eglSubDriver","libvulkan","vulkan.adreno","libllvm-glnext","libart.so","libhwbinder","linker","vdso","libnextvm"}
};
```

**决策顺序（2026-11-28 修正：`pkg` 整体先于 `lib`）**：

```
0) lib_name == nullptr → 不过滤（保持现状：照常 patch）
1) pkg ∈ pkg_deny                    → SKIP           ← 整包豁免，最高优先级
2) pkg ∈ pkg_allow:                                   ← 先定"这个包走哪套规则"
     a) lib ∈ lib_allow               → PATCH          ← 白名单可覆盖 lib_deny
     b) lib ∈ lib_deny                → SKIP
     c) 其他                          → 常规四级判据（app 库 patch / 系统库按 NS_*）
3) pkg 未列入任何包表:
     a) lib ∈ lib_deny                → SKIP           ← 全局默认仍禁危险库
     b) 其他                          → 常规四级判据
     （lib_allow 不在此生效：白名单必须包级限定，避免"一处加白名单、全容器放大"）
```

**为什么 `pkg` 必须先于 `lib`**：`pkg_allow` 存在的意义是"限定作用域"。若先判 `lib`，
一个 `lib_allow` 条目就会对所有 guest 生效（等于变相打开 `NS_HOOK_SYSTEM_LIBS`）；先判 `pkg` 后，
"via 里 patch WebView 库"与"其他 guest 完全不受影响"可以同时成立。

**关键点**：
- `lib_deny` 把现在**硬编码的图形栈六条 + callback 层那五项**收进来（数据化，可审计）；
- `lib_allow` 能覆盖 `lib_deny`（这是用户要的能力），但**风险极高**（图形栈被 patch = RenderThread SIGABRT 的老路）⇒ 只能在 `pkg_allow` 限定的 guest 内生效；
- `pkg_allow` 的作用是**限定作用域**，不是"该包全部库都 patch" —— 否则 via 里所有系统库都会被 patch。

---

## 三、初始配置（本批唯一要开的）

| 表 | 值 | 说明 |
| --- | --- | --- |
| `pkg_allow` | `mark.via` | 只对 via 生效 |
| `lib_allow` | `libwebviewchromium`<br>`libwebviewchromium_plat_support`<br>`libmonochrome` | WebView 三件套（不同 ROM/版本命名不同，用**子串**匹配） |
| `pkg_deny` | 空 | — |
| `lib_deny` | 现有 graphics 六条 + `libart.so` / `libhwbinder` / `linker` / `vdso` / `libnextvm` | 从硬编码迁进来 |

---

## 四、配置怎么进 native（通路）

| 方案 | 做法 | 评价 |
| --- | --- | --- |
| **A（推荐）** | Kotlin 侧 `LibFilterDefaults` 定义默认表（可单测）→ 启动时经新 JNI 导出 `nativeSetLibFilter(...)` 传给 native（存为全局，加锁） | 表可版本化、可单测、可在 bind 前生效 |
| B | native 内置硬编码表 | 简单但不可测、改一次要重编 |
| C | `setprop debug.nextvm.libAllow=…` | 适合临时排查，不适合当主通路（长度限制、转义麻烦） |

**新增 JNI 导出**（第 **26** 个）：`nativeSetLibFilter(pkgAllow, pkgDeny, libAllow, libDeny)`（四个字符串，逗号分隔）。
调用时机：`initNativeHooks()` 之后、任何 guest bind 之前（`VirtualEngine` 初始化处）。

---

## 五、集成点（改动清单）

| 文件 | 改动 |
| --- | --- |
| `core/lib_filter.h` | 加 `LibFilterConfig` 结构与 `classify_lib(lib, cfg, pkg, system_libs_enabled)` 新重载 |
| `core/lib_filter.cpp` | 实现 §二 的 5 步决策顺序；把 graphics 六条从硬编码迁入 `lib_deny` 默认值 |
| `native-hook.cpp` | 门面持有 `g_lib_filter_cfg`（加锁）；`patch_library_got` 传 `g_spoofed_package_name` 作 pkg；新增导出 `nativeSetLibFilter` |
| `NativeHookBridge.kt` | `external fun nativeSetLibFilter(...)` + 包装 |
| `LibFilterDefaults.kt`（新） | 默认四张表 + 单测 |
| `core/hook/src/test/.../lib_filter_test.cpp` + 向量 | 新增**优先级用例**：deny 命中但 allow 也命中 → patch；allow 命中但 pkg 不在 pkg_allow → skip；pkg_deny 整包 skip |
| CMakeLists / check-jni-contract | 新导出登记（25 → 26） |

---

## 六、判据（能不能算成功）

| 层 | 判据 |
| --- | --- |
| host 测试 | 新增 ≥8 条优先级向量全过；既有 20 条不回归 |
| `check-jni-contract` | Kotlin 26 ↔ C++ 31（5 个旧符号照旧） |
| 真机（via 进程） | `hook stats` 的 `applied` 从 0 变 **≥1**、`slots` ≥6（说明 `libwebviewchromium` 真被打上）；`skipped_syslib` 相应 −1 |
| 真机（probe 进程） | **不变**（白名单只对 via 生效）—— 反向判据 |
| **功能** | `W-ext-synthpath` 从 `EMPTY` 变 `text/html`（最直接判据）；via 主页正常渲染、不崩 |
| 稳定性 | WebView 预热/首屏不崩（历史 `webviewwarm`/`webviewwatch` 日志可对照） |

---

## 七、风险与对策

| 风险 | 对策 |
| --- | --- |
| `libwebviewchromium.so` 的 GOT 受 **RELRO/BIND_NOW** 保护 → 槽写不进去 | 先在真机探测：`patch_got_entry` 已带 `is_address_readable` + `mprotect` 失败日志；探测失败就记"不可 patch"，转 inline hook 讨论 |
| patch 后 WebView 崩（Chromium 热路径） | 白名单只对 via 生效，probe 不受影响；崩溃时一键清空 `pkg_allow` 回退 |
| 翻译规则改坏 Chromium 的资源读取（`/system`、`/proc`、字体） | 翻译**只对 `/data/user/0/<guest>/` 前缀生效**（A3 白名单式翻译），其余原样 |
| 白名单覆盖 `lib_deny` 重现 SIGABRT | `lib_allow` 与 `lib_deny` 的交集只允许**显式逐库**添加，并在 `lib_filter_test` 里固化"当前允许的交集"；`libGLES/libEGL` 永不进 `lib_allow` |
| 多 ROM 的 WebView 库名不同 | `lib_allow` 用**子串**（`libwebviewchromium` 覆盖 `libwebviewchromium.so` 与 `_plat_support`），并把 ROM 差异写进文档 |

---

## 八、分批（从轻到重，每批可独立验证）

| 批 | 内容 | 判据 | CI |
| --- | --- | --- | --- |
| **G1** | 数据结构 + `classify_lib` 改造 + 默认表 + host 向量（**不真机开**，`pkg_allow` 先留空） | host 测试全过、既有 20 条不回归 | `module-test`（含 `cpp-core-test`） |
| **G2** | JNI 导出 + Kotlin 接线（`nativeSetLibFilter`），`pkg_allow` 仍空 → 真机确认"零行为变化" | `hook stats` 与基线逐字段一致 | `module-test` + `container-build` |
| **G3** | 打开初始配置（via + webview 三件套）+ 假→真路径映射规则 | `applied`≥1、`W-ext-synthpath` 变 `text/html`、via 正常 | `container-build` + 真机 |

> G1/G2 是**纯重构 + 零行为变更**（把硬编码迁进表、加通路但不开）；G3 才是行为变更，届时单独写变更文档。

---

## 八之二、权威源码与解包结论（2026-11-28 取证）

### 1) bionic `link.h`（AOSP 主干，权威）

`struct dl_phdr_info` 里 **`dlpi_adds` / `dlpi_subs`** 的定义（原文要点）：

- `dlpi_adds` = "The total number of **library load events** at the time `dl_iterate_phdr()` was called"
- `dlpi_subs` = 同理，unload 事件
- **仅 API level 30+**；用回调的 `size` 参数判断能否读到这几个字段
- 源码：<https://raw.githubusercontent.com/aosp-mirror/platform_bionic/main/libc/include/link.h>

⇒ **方案 C（`dlpi_adds` 轮询）有官方依据**；旧版本（<30）回退定时触发。
同时头文件里 `struct r_debug` 的注释是 **"Used by the dynamic linker to communicate with the debugger"**，
`r_brk` 是调试器 rendezvous ⇒ **方案 D 会干扰调试器**（加固壳若用反调试会立刻发现），这一条也坐实。

### 2) Chromium `net/base/mime_sniffer.cc`（权威）

- 嗅探的判据是**魔数表**（`CheckForMagicNumbers`），HTML 文本没有魔数 ⇒ 嗅探不出 `text/html`；
- 文件头注释明确"HTML payload **with no Content-Type** ⇒ render as HTML"（浏览器默认），
  但"无魔数 ⇒ 嗅探返回 null ⇒ 上层用默认值"这条路径会落到 `text/plain`。
- 源码：<https://raw.githubusercontent.com/chromium/chromium/main/net/base/mime_sniffer.cc>

⇒ 与实测完全一致：**嗅探不出 HTML ⇒ `text/plain`**；**按扩展名**（`net::GetMimeTypeFromFile`）才得到 `text/html`。
> **仍未确认的机制**：为什么"URL 含宿主包名"会走嗅探分支（而不含则按扩展名）。
> 这属 WebView（`android_webview/`）的 file-access 策略，未取到权威源码；
> 但**规律本身有 9 组实测**（见 `主题/册-WebView与file-MIME.md`），设计按规律走、机制标注"未确认"。

### 3) 解包结论（少拆：只看目录与关键文件）

| 对象 | 结论 |
| --- | --- |
| **via APK**（2 628 334 B，364 条目） | **无 `lib/` 目录 ⇒ 纯 Java，无 native 库**（这解释了 `applied=0`）；`assets/` 只有 `error.html` / `logo.svg` / `opensug2.js` / `simple.txt` / `dexopt/*`；**首页 HTML 不在 assets**（运行时写到 `getFilesDir()` 再由 `file://` 加载） |
| **WebView APK** | `/product/app/WebViewGoogle64/WebViewGoogle64.apk`（**74.5 MB**），`lib/arm64-v8a/` **未解压** ⇒ 库路径形如 `…WebViewGoogle64.apk!/lib/arm64-v8a/libwebviewchromium.so` |
| 包/版本 | `com.google.android.webview` 126.0.6478.71（`dumpsys webviewupdate`） |

⇒ 设计含义：
- `lib_allow` 用**子串** `libwebviewchromium` 即覆盖 `.so` 与 `_plat_support`（不同 ROM 命名差异靠子串吸收）；
- 该库路径含 **`/product/app/`**（不是 `/data/app/`）⇒ 当前 `classify_lib` 判为 `system_lib` 并跳过 —— **正是要白名单化的对象**。

1. **初始 `lib_allow` 清单**：`libwebviewchromium` / `libwebviewchromium_plat_support` / `libmonochrome` —— 是否够？本机是 `com.google.android.webview 126.0.6478.71`（`/product/app/WebViewGoogle64/`）。
2. **`pkg_allow` 是否只放 `mark.via`**（探针 `com.sandbox.probe` 不加，作为反向判据）。
3. **G3 的翻译规则**是否就按"`/data/user/0/<guest>/files/` → `/storage/emulated/0/vsandbox/<guest>/`"这一条**只对 file:// 内容**生效（其他路径不动）。
