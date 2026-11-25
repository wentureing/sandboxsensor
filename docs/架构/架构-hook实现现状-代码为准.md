# 架构：hook 实现现状（**以代码为准**）

- 日期：2026-11-25
- 基线：容器 `baseline-v150 @ 6b5cfbe`；上游对照 `woand/ShitJumpingVM @ 63346e6`（= 我们的 fork 点）
- 用途：**改 hook 前必读**。多篇旧架构文档的"hook 清单/覆盖矩阵"已落后于代码，本文给出代码级事实。
- 上位判据：`docs/设计/设计-项目设计理念.md`；路径匹配语义见 `docs/输出/记录/记录-T39-T41-族根路径无规则.md` 的方案讨论

---

## 一、先记住三条判据

| # | 判据 | 为什么 |
| --- | --- | --- |
| **1** | **「实现了」≠「生效」** | 三层开关决定实际覆盖：编译期宏 `NS_HOOK_SYSTEM_LIBS` + `debug.nextvm.javaIo` + `debug.nextvm.javaIoTranslate` |
| **2** | **覆盖分两级** | ①**库级**：哪些 `.so` 的 GOT 被 patch；②**符号级**：每个被 patch 的库里替换了哪些函数 |
| **3** | **层间必须一致** | `架构-hook关键路径.md` 的判据"同一路径 Java 与 native 算出同一结果"；当前**默认配置下并不满足**（见 §五） |

---

## 二、native GOT：**27 条符号**

位置：`core/hook/src/main/cpp/native-hook.cpp:1679-1705`。上游 `63346e6` 是 **22 条**，本仓库增补了 5 条写操作。

### 2.1 走路径翻译（`redirect_path`）的

| 符号 | hooked 函数 | redirect 调用点 | 备注 |
| --- | --- | --- | --- |
| `open` / `open64` | `hooked_open` | `:674` | 含 `/proc/self` 伪造分支（`:666-672`） |
| `openat` / `openat64` | `hooked_openat` | `:710` | 相对 dirfd 也翻译 |
| `access` | `hooked_access` | `:736` | `File.exists` 底层 |
| `stat` | `hooked_stat` | `:751` | |
| `lstat` | `hooked_lstat` | `:766` | |
| `readlink` | `hooked_readlink` | `:792` | `/proc/self/fd/N` 反查 |
| `fopen` / `fopen64` | `hooked_fopen` | `:898` | stdio 路径 |
| `opendir` | `hooked_opendir` | `:1374` | 目录列举 |
| **`mkdir`** | `hooked_mkdir` | `:1390` | **只建最后一级**（AOSP 语义） |
| **`mkdirat`** | `hooked_mkdirat` | `:1406` | |
| **`unlink`** | `hooked_unlink` | `:1418` | |
| **`unlinkat`** | `hooked_unlinkat` | `:1434` | |
| **`rename`** | `hooked_rename` | `:1446-1447` | 源与目标**各自翻译** |

**共 13 个函数体 / 14 个符号。** 加粗的 5 条是本仓库相对上游新增——
「容器重定向的是文件操作、不是目录操作」这条旧结论**已作废**。

### 2.2 不走路径翻译的

| 符号 | 作用 |
| --- | --- |
| `__system_property_get` | 属性伪造（`getprop` 的 native 入口） |
| `dlopen` / `android_dlopen_ext` / `dl_iterate_phdr` | 运行期新加载库的 patch 钩子（L6，当前链断） |
| `ptrace` / `syscall` / `fork` | **只打日志**，原样透传 |
| `mmap` / `mprotect` | 独立 patch 项（`:2142-2143`），用途待确认 |
| `connect` / `getaddrinfo` | 网络，路径无关 |

### 2.3 观测点

`open/stat/mkdir/opendir/unlink/rename` 各有 `total / translated / fail` 三个计数器，
由 `nativeGetFsStats()`（`:2314`）输出。**判断"某操作到底有没有被翻译"用这个，不要靠日志猜**。

---

## 三、Java 层 `RegisterNatives`：16 个方法（**默认不安装**）

位置：`install_java_io_hooks`（`:7719-7839`）。

| 类 | 方法（8 个） |
| --- | --- |
| `libcore.io.Linux` | `open`, `access`, `mkdir`, `unlink`, `rename`, `stat`, `lstat`, `fstat` |
| `java.io.UnixFileSystem` | `list0`, `createDirectory0`, `createFileExclusively0`, `getBooleanAttributes0`, `getLastModifiedTime0`, `setLastModifiedTime0`, `setReadOnly0`, `canonicalize0` |

**为什么必须两条都在**：`File` 走 `FileSystem`→`UnixFileSystem.*0`，`android.system.Os` 走 `Linux.*`，
只 hook 一条会漏掉另一半（见 `架构-为什么native在UnixFileSystem与跨版本兼容.md`）。

**开关**（`JavaIoHooks.kt:59-65`）：

| 开关 | 默认 | 含义 |
| --- | --- | --- |
| `debug.nextvm.javaIo` | **false** | 不安装（等价于没有这套 hook） |
| `debug.nextvm.javaIoTranslate` | true | 安装后是否真的翻译；false 只统计 |

**语义等价是硬要求**：A 族（`Linux.*`）失败抛 `ErrnoException`、B 族（`UnixFileSystem.*0`）返回 `null/false/0`。
v141 只对齐成功路径 → Via 完全不可交互（`docs/输出/记录/记录-JavaIoHook导致Via不可交互.md`）。

---

## 四、库级过滤：范围**过大**与**过小**都出过事

位置：`patch_library_got` 的过滤链（`native-hook.cpp:1800-1855`）。

### 4.1 当前四级判据

| 优先级 | 类别 | 判据 | 处置 | 理由 |
| --- | --- | --- | --- | --- |
| 1 | 容器自身 | 路径含 `com.nextvm.app` 或 `libnextvm` | **永不 patch** | 重定向实现依赖真实的 `open/stat`；拦自己会死循环/错改宿主 IO |
| 2 | 图形栈 | `libGLES*`/`libEGL*`/`eglSubDriver*`/`libvulkan*`/`vulkan.adreno*`/`libllvm-glnext*` | **永不 patch** | 实测 patch 后 shader 编译 abort（RenderThread SIGABRT） |
| 3 | 应用/沙箱库 | 路径含 `/data/app/`、`/data/user/`、`/virtual/` | **始终 patch** | 这才是多数的"guest 自己的 native 代码" |
| 4 | 其余系统库 | 以上都不是 | 由 `NS_HOOK_SYSTEM_LIBS` 决定，**默认 0 = 不 patch** | 系统库的运行前提不能被改坏 |

### 4.2 与上游的对照（`63346e6`）

| 维度 | 上游 | 现在 |
| --- | --- | --- |
| hook 符号数 | 22 | 27 |
| 系统库过滤 | **无**（只排除 `libnextvm`/`libart.so`/`libhwbinder`/`linker`/`vdso`/CM 的 `libDexHelper.so`） | 四级判据 + `NS_HOOK_SYSTEM_LIBS=0` |
| 图形栈 | **被 patch**（因为全 patch） | **永不 patch** |
| 结果 | 渲染异常 | 渲染正常 |

**结论：上游"过滤语义"（先排除绝对不能动的，再全量 patch）是对的思路，但它的排除表不完整——
漏了图形栈。** 直接回退到上游会重演渲染异常。

### 4.3 范围"过小"的那次事故（已解决，但要知道为什么）

`NS_HOOK_SYSTEM_LIBS=0` 是一次性排除**所有**系统库，其中包括 `libwebviewchromium*.so`。
后果（`3bef8fe` 的实测记录）：

- Via 主页（本地 HTML）被当纯文本显示；
- Via 里长按文字 → 选中区出现但无法交互 → ANR；
- **WebView 浏览时 popup 闪退**。

当时的修法是"精确白名单"：在过滤链第 3 步后加 `is_webview = libwebviewchromium*` 强制 patch
（`3bef8fe`，位于备份分支 `backup-webview-hook`，**未并入 baseline**）。

**当前版本为什么 popup 与渲染都正常**（两条独立措施，不要混为一谈）：

| 症状 | 措施 | 位置 |
| --- | --- | --- |
| 渲染异常 | 图形栈永不 patch | `:1827-1837` |
| WebView 读不到本地 HTML / popup 异常 | **内容目录改用真实沙箱路径**（不需要翻译 → 不依赖任何库被 patch） | `架构-WebView假路径换真路径.md`、`VirtualContext.kt:61` |

**所以"webview 白名单"不是当前生效方案**——它被"真路径"方案取代了。
若将来改回合成路径（内置 WebView 之后），必须重新评估 webview 库是否要 patch。

**用户实测（2026-11-25，判定者 = 用户）**：当前版本 **popup 正常、渲染正常**。
这条是两边范围的验收判据：范围再放大 → 渲染异常复发；再缩小 → popup 异常复发。

### 4.4 一条不可谈判的约束

> **不要为了"覆盖更多"而打开 `NS_HOOK_SYSTEM_LIBS=1`。**
> 那是把范围整体放大回上游的形态（图形栈除外，但仍会 patch 大量系统库），历史上已导致渲染异常。
> 需要覆盖某个系统库时，走**逐个精确白名单**（如 `3bef8fe` 的写法），并记录理由。

---

## 五、真实生效面：默认配置下是「双层不一致」

```
默认（NS_HOOK_SYSTEM_LIBS=0, javaIo=false）
  app 自带 .so 直接调 libc        → GOT 被 patch → 落沙箱      ✓
  Java 层 File / FileInputStream  → libjavacore 未被 patch
                                    且 RegisterNatives 未安装   → 落宿主真实路径 ✗
```

**推论（重要）**：

1. "宿主出现空目录壳（via 的 `filters/font/gm/logs/...`）+ 沙箱里有部分文件"不是巧合，
   而是 **Java 层建目录落宿主、native 层写文件落沙箱** 这个结构差。
2. `hooked_open` **没有 `ENOENT` 兜底**（`:674-688` 直接 `real_open`）。
   "父目录缺失 → 写入静默失败"仍然可能，但触发条件是**建目录与打开文件走了不同层**，
   不是"`mkdir` 没被 hook"。
3. 把内容目录改成真路径（`contentPathsUseReal=false`）之所以有效，正是因为它**绕开了层间分歧**：
   真路径不需要任何翻译。

---

## 六、未覆盖与已知缺口

| # | 缺口 | 事实 | 影响 |
| --- | --- | --- | --- |
| 1 | **NIO2** | `sun.nio.fs.UnixNativeDispatcher.*` **0 实现**（全仓 grep 无匹配） | `Files.*` / `Path` 的调用不被重定向。旧文档把它写成"用 RegisterNatives 覆盖"，那是**计划不是现状** |
| 2 | **L6 运行期加载库** | `libnativeloader.so` 未被 patch → `patch_newly_loaded_libraries` 不触发 | 新加载的库里的 libc 调用不被拦（P28 实测写穿到真实 `/sdcard`） |
| 3 | **L7 直接 syscall** | `hooked_syscall` 只打日志 | 只能靠"伪造数据源"覆盖，不单独拦 |
| 4 | `hooked_open` 无 `ENOENT` 兜底 | `:674-688` | 跨层时静默失败，用户不可察觉 |
| 5 | `hiddenPaths` 无 native 精确匹配 | C++ 只有前缀表 `g_path_redirects` | Java 侧 hide 了、native 侧不知道 |
| 6 | L8/L9/L10 | 不可能 | isolated 进程、继承 fd、同 uid DAC |

---

## 七、改 hook 前的自查清单

| # | 问题 | 不回答的后果 |
| --- | --- | --- |
| 1 | 这条改动落在**库级**还是**符号级**？ | 改错层等于没改（例：为覆盖 WebView 去改符号表，实际问题是库被跳过） |
| 2 | 在**默认开关**下生效吗？ | 写了但默认关 = 线上无效 |
| 3 | 目标库属于哪一级（自身/图形栈/应用/系统）？ | 图形栈类改动必崩；系统库类改动必须走精确白名单 |
| 4 | 被排除的库会不会因此读不到沙箱？ | 这正是 popup 闪退与渲染异常的来源 |
| 5 | Java 与 native 两侧结果一致吗？ | 不一致 = 写到一个地方、从另一个地方读 |

---

## 八、本文取代的过时结论（引用前对照）

| 旧文档 | 过时结论 | 现在的正确结论 |
| --- | --- | --- |
| `架构-hook路径覆盖矩阵与缺口.md` | 「`mkdir`/`mkdirat` 无 hook；容器重定向的是文件操作不是目录操作」 | 5 条写操作全部已 hook 且走 `redirect_path`（§2.1） |
| `架构-主原生guest全hook.md` | 「21 个符号，建议砍到 14」 | 实际 **27 个**，一个没删；该文是**建议，未执行** |
| `架构-拦截能力层次.md` / `架构-hook关键路径.md` | 「L4 Java 层文件 API 是空壳，`hookFileClass()` 只打日志」 | 16 个 `RegisterNatives` 方法已实现，**默认不安装** |
| `架构-syntheticDataDir.md` §3.2 | 「`getExternalMediaDirs()` 未加分支」 | 已有分支（`VirtualContext.kt:217`，`54f3649` 补） |
| `架构-为什么native在UnixFileSystem与跨版本兼容.md` §2.2 | 「L3 `UnixNativeDispatcher` 用 RegisterNatives 覆盖」 | **0 实现**，是缺口（§六 #1） |
