# 现状：hook 摸底 · 能力边界 · 功能使用频次（2026-11-27）

- 回答四个问题：
  1. 现在 hook 了什么、分几类、各类明显缺陷（**重点 IO**）；
  2. 更深的欺骗要付出什么代价、难在哪；
  3. 能不能**只在一层全部骗下**，避免开发铺开；
  4. 哪些功能开发了但没被用上、哪些很常用。
- 取数：设备 `10AFA8169Z0039G`、容器 `1.0.0-ecfb4e5`；
  `debug-trace.log`（1.43 MB）与 `checks.log`（112 KB）为 2026-11-27 现场样本。
- 关联：`架构-hook实现现状-代码为准.md`（代码级事实）、`设计-项目设计理念.md`、`计划-可运行优先与C-dual.md`

---

## 一、hook 清单与分类（9 类）

| # | 类 | 手段 | 覆盖面 | 默认 | 主要落点 |
| --- | --- | --- | --- | --- | --- |
| **H1** | **native libc GOT/PLT** | 改写 `.rela.plt` 的 GOT 槽 | **27 条符号**（14 条走路径翻译 / 13 条不走） | ✅ 开 | `native-hook.cpp` + `core/path_spoof.cpp` |
| **H2** | **Java native 方法替换** | `RegisterNatives` | **16 个方法**：`libcore.io.Linux` 8 个 + `java.io.UnixFileSystem` 8 个 | ❌ **默认关**（`javaIo=0`） | `install_java_io_hooks` |
| **H3** | **系统服务 / Binder 代理** | 换 `ServiceManager` 里的 Binder 代理 | `SystemServiceProxyManager`、`ActivityManagerProxy`、`PackageManagerProxy`、`ContentResolverProxy`、`ActivityBinderWrap`、`IntentParcelSanitizer` 等 **9 个类** | ✅ 开 | `core/binder` |
| **H4** | **ActivityThread 消息泵** | hook `mH` 的 handler | `launch`/`txn`/`create`/`stop` 等消息 | ✅ 开 | `ActivityThreadHook.kt` |
| **H5** | **Instrumentation 换装** | 替换 `mInstrumentation` | `newApplication`/`newActivity`/`callActivityOnCreate` | ✅ 开 | `VirtualApplicationManager` |
| **H6** | **ClassLoader / LoadedApk 注入** | 换 `mClassLoader`、改 `LoadedApk` 路径 | guest 启动链 | ✅ 开 | `startup/GuestLoadedApkPathInjection` 等 |
| **H7** | **`/proc` 伪造** | memfd 伪造内容 + `prctl` 改 comm | `cmdline`/`comm`/`status`/`maps` | ✅ 开 | `native-hook.cpp` + `ProcSpoofing.kt` |
| **H8** | **seccomp 归因/监督** | `SECCOMP_RET_TRAP`（SIGSYS）+ **`SECCOMP_RET_USER_NOTIF`** | 指定 syscall 集（openat/fstatat 等） | ⚠️ 诊断用途，按 flag 启用 | `native-hook.cpp:2529+` |
| **H9** | **属性伪造 / 崩溃守卫** | `__system_property_get` GOT + main-looper 守卫 | 指定属性、`killProcess` 拦截 | ✅ 开 | 同上 + `ProcessKillGuard` |

### H1 的 27 条符号（`g_hook_entries`）

- **走路径翻译（14）**：`open`、`open64`、`openat`、`openat64`、`access`、`stat`、`lstat`、`readlink`、`fopen`、`fopen64`、`opendir`、`mkdir`、`mkdirat`、`unlink`、`unlinkat`、`rename`（含 64 变体共 14 个槽名）
- **不走翻译（13）**：`__system_property_get`（属性）、`dlopen`/`android_dlopen_ext`/`dl_iterate_phdr`（延迟 patch 触发）、`ptrace`/`syscall`/`fork`（**只记日志**）、`mmap`/`mprotect`（独立项）、`connect`/`getaddrinfo`（网络）

---

## 二、IO 相关的明显缺陷（重点）

| # | 缺陷 | 现象 | 根因 | 影响 |
| --- | --- | --- | --- | --- |
| **IO-1** | **Java 层 IO 不翻译**（默认） | `File.listFiles/mkdirs/renameTo` 落宿主或失败 | `javaIo=0`；`File` 走 **libjavacore.so**（系统库，`NS_HOOK_SYSTEM_LIBS=0` → 不 patch） | **IO 最大缺口**：纯 Java app 只能靠 `VirtualContext` 给的真路径兜住 |
| **IO-2** | **NIO2 整族未覆盖** | `java.nio.file.Files.*` 不重定向 | `UnixNativeDispatcher` **0/38** 未替换 | app 用 NIO 写宿主真实路径 |
| **IO-3** | **`readlink` 泄露** | guest 可把沙箱路径换算成真实路径 | A 族 `readlink` 未 hook（native 侧有，Java 侧无） | 反虚拟化检测；也是 IO-2 的旁路 |
| **IO-4** | **动态加载的库抓不到/抓晚** | bind 后 `System.loadLibrary` 的库不 patch | `libnativeloader.so` 不 patch + `nativeLoad` hook 未生效 | 已用 +5s/+20s 兜底（`applied=1 slots=12`），但更晚加载仍漏 |
| **IO-5** | **`dlsym` / raw syscall 完全绕过** | 拿地址直接调、或 `syscall(SYS_openat)` | GOT patch 本质是"改符号解析结果" | 见 §四，属**原理边界** |
| **IO-6** | **`hooked_open` 无 `ENOENT` 兜底** | 父目录在不同层创建 → 静默失败 | 层间不一致（Java 建目录落宿主、native 写文件落沙箱） | 少见但难查 |
| **IO-7** | **`hiddenPaths` 无 native 精确匹配** | Java 侧隐藏了，native 不知道 | C++ 只有前缀表 | 低优先 |
| **IO-8** | **`mmap`/`mprotect` 只 patch 不入统计** | 观测盲区 | 独立 patch 项，无计数 | 排查时看不到 |

> **IO 的结论**：现状的正确性是"**三层拼出来的**"——
> `VirtualContext`（给真路径/合成路径）+ GOT patch（app .so 的 libc）+ （可选）`javaIo`（Java 层）。
> 任何**绕过这三层**的写法都漏；这就是 §四 要回答的"能不能一层全骗"。

---

## 三、更深的欺骗：代价与难度

| 欺骗级 | 内容 | 代价 | 难度来源 |
| --- | --- | --- | --- |
| L1 路径形状 | 合成 `ApplicationInfo` 字段、maps 行改写 | 低：已有（`pathFidelity`/`transform_maps_line`） | — |
| L2 身份 | `/proc` 三件套、属性覆盖、uid 视角 | 中：已有（`ProcSpoofing`），需持续跟 AOSP 字段 | 字段随 Android 版本变化（`status` 字段、`NSpid` 等） |
| **L3 深度** | Build / SELinux / mount / root 痕迹 / 常见风控 SDK 的特征 | **高** | ① **样本量**：每个检测点都要"看起来对"，且要覆盖多个厂商 ROM；② **自洽**：同一事实必须在 5–10 个 API 上一致（如"这个设备是小米"要同时满足 Build、属性、系统应用列表、OTA 字段）；③ **可维护**：每升一个 Android 版本就要重测；④ **对抗**：检测方会交叉验证（如 maps vs `/proc/self/fd` vs `statfs`） |

**难度的本质不是"写多少代码"，而是"一致性成本"**：
骗 1 个字段很便宜，但要让 N 个字段互相自洽，成本是 **O(N²) 的交叉检查**。
所以 L3 的正确做法不是"逐个硬编码"，而是 **§计划里的"采样驱动"**：从真实环境采样一次，所有欺骗面都从同一份采样生成 —— 这样自洽性是"构造保证"，不靠人记。

---

## 四、能否"只在一层全部骗下"

四个候选层，逐个评估：

| 层 | 覆盖 | 免 root | 代价 | 结论 |
| --- | --- | --- | --- | --- |
| **A. Java Context 层**（`VirtualContext` 返回值） | 只覆盖"经 Context 拿路径"的调用 | ✅ | 极低 | ❌ 不能全骗：guest 自己拼路径就绕过 |
| **B. libc PLT 层**（现状 H1） | app .so 的静态 PLT 调用 | ✅ | 低（已实现） | ❌ 不能全骗：`dlsym`/syscall/JIT 绕过 |
| **C. seccomp 层**（`RET_TRAP`+SIGSYS / `RET_USER_NOTIF`） | **所有** syscall（含 dlsym 拿到的地址、raw syscall、JIT） | ✅ **已实现基础** | **高** | ✅ **唯一能"一层全骗"的**，但代价大（见下） |
| **D. mount namespace / bind mount** | 全部（不依赖 hook） | ❌ **需 root** | — | ❌ 不可行 |

### C 已具备什么（代码事实）

- `SECCOMP_RET_TRAP` + SIGSYS handler：**已能在 handler 里改写路径参数**（`sc_rewrite_fd_target`，`native-hook.cpp:3936`），且约束是"无分配、无 libc"；
- `SECCOMP_RET_USER_NOTIF` + listener：**已实现**（`:3438`、`:3526` 等，含 v89/v91 变体）；
- 已有 seccomp 模块表/归因（`seccomp-diag.log` 每进程一份，14 个文件）。

### C 的代价（为什么现状只把它当"诊断"）

| 代价 | 具体 |
| --- | --- |
| **性能** | 每个被拦 syscall 都要陷入用户态（SIGSYS 或 listener 唤醒），量级从 ~1 µs 涨到 10–50 µs；`open/stat/access` 是高频，全量拦会显著拖慢 guest |
| **语义风险** | SIGSYS 与目标进程自己的信号处理冲突（代码里 v72/v89 已在处理"信号被屏蔽/被改"）；`USER_NOTIF` 还要求 supervisor 进程/线程存活，guest 卡住的代价更高 |
| **可被检测** | 加固壳能读 `/proc/self/status` 的 `Seccomp`/`Seccomp_filters`（项目已专门做 watchdog 观测这点）→ "有 filter"本身就是特征 |
| **实现约束** | handler 里**不能分配内存、不能调 libc**（`sc_streq`/`sc_strstr` 都是自写），逻辑复杂度远高于普通 GOT hook |
| **覆盖面不等于正确性** | 即使拦到 syscall，"该怎么改写"仍然依赖同一套路径规则（B 层的 `redirect_path`）—— 所以 C 不是"替代 B"，而是"B 的兜底" |

### 结论（第 3 问的答案）

> **能，但只应该"在少量高频入口上全骗"，而不是"全量 IO 全骗"。**
>
> - 若目标是"让 MT 这类文件管理能跑"：**B（GOT）+ C-dual 真路径**足够，不需要 C；
> - 若目标是"连 dlsym/raw syscall 都拦"：只能在 **C 层**做，且必须限定 syscall 集（例如只拦 `openat`/`faccessat`/`newfstatat`），并接受性能与可检测代价；
> - **不要**指望"在某一层全部骗下"能省掉路径规则：无论哪层，都要有"路径该翻成什么"的规则（现在的 `path_rules`/`path_spoof` 就是它）。

---

## 五、功能使用摸底（真机现场数据）

`2026-11-27` 的 `debug-trace.log`（1.43 MB）与 `checks.log`（112 KB）：

### 5.1 高频（= 真的在用，改动要有回归）

| 模块 / 事件 | 次数 | 说明 |
| --- | --- | --- |
| `[Dbg/path]` | **1318** | 路径翻译/规则命中 —— 最热 |
| `[Dbg/ctx]` | 598 | `VirtualContext` 路径给出 |
| `[Dbg/sandbox]` | 438 | 沙箱目录管理 |
| `[Dbg/ensureDir]` | 360 | 目录创建 |
| `replaceClassLoader` | 60 | 启动链 |
| `v118-provider-fields` | 58 | provider 元数据 |
| `[Dbg/initNativeHooks]` | 54 | native 初始化（每次进程启动） |
| `v129-autofill` | 45 | Activity 级行为抑制 |
| `[Dbg/native]` | 43 | hook stats（bind + 2 拍 × 进程数） |
| `v116-plugin-probe` | 40 | 插件化探测 |
| `bind complete` | 36 | bind 收尾（每次启动 1） |
| `v53 probe` | 32 | ClassLoader 探测 |
| `[Dbg/appInfo]` | 28 | `ApplicationInfo` 改写 |
| `provider ok` / `FAILED` | 22 / 7 | provider attach |
| `v142-redirect` / `v143-datadir` | 20 / 20 | 规则注册 / datadir patch |
| `v81 watch` / `v130-watchdog` | 20 / 20 | 文件监视 / 看门狗 |
| `S crumb`（checks.log） | 551 | breadcrumb 镜像 |

### 5.2 低频 / 未观察到（= 可能没被用上，或只对特定 guest 生效）

| 项 | 现场次数 | 判定 |
| --- | --- | --- |
| `seccomp`（debug-trace 里） | **0** | seccomp 走独立文件 `seccomp-diag.log-<pid>.log`（14 份，约 30 KB/份），不在 debug-trace |
| `legacy attachBaseContext` | 8 | 只在 `frameworkBind=0` 时出现（我们采 golden 时用的开关）→ **生产默认不走** |
| **5 个未调用的 JNI 导出** | — | `nativeGetRecent` / `nativeClearRecent` / `nativeGetStats` / `nativeResetStats` / `nativeInstall` —— `check-jni-contract` 一直提示为"多余符号"，**Kotlin 侧无对应 external fun** |
| **CM 专用门控**（`v87/v89/v90b`、`v86 maps-real`） | 0（非 CM guest） | 只有 `com.greenpoint.android.mc10086.activity` 会命中 → 对普通 guest 是**死代码** |
| `PropTrace`（v32） | 0 | 记录型诊断，未启用 |
| 探针 `P`/`N` 系列（`suite=round` 下） | 47 项 skipped | 默认只跑 `J/F/D`；`P`/`N` 需要显式 `suite=all` |
| `javaIo` 的"翻译"路径 | 99 行日志，但 `installed=false` | 默认关（`javaIo=0`），只有 SKIPPED 记录 |
| `nativeGetFsRecent` | 调用但内容空 | `recent opens after bind:` 为空（bind 时还没有 IO） |

### 5.3 明显的"开发了但没接上 / 不生效"

1. **`Runtime.nativeLoad` hook**（上游 `7cdcf01` 就有）—— 从未装成功（无 SUCCESS 日志），属"写了没生效"；
2. **5 个 JNI 导出**无 Kotlin 调用方；
3. **CM 专用分支**（v86/v87/v89/v90b）对非 CM guest 完全不走；
4. **`javaIo` 整套（16 个 RegisterNatives）**默认关 → 等于"长期不使用"；
5. **NIO2（38 个方法）**从未实现（设计取舍）。

---

## 六、结论与建议（对应四个问题）

1. **hook 共 9 类**，IO 相关的核心是 H1（GOT，27 条）+ H2（Java 16 个，默认关）+ H6/H7；
   最大 IO 缺陷是 **IO-1（Java 层不翻译）** 与 **IO-2（NIO 0/38）**，且二者都源于"系统库不 patch"这一 fork 后的决策 —— 所以**路径策略（C-dual）比继续加 hook 更划算**。
2. **更深欺骗（L3）的代价是"一致性成本"而不是代码量**；正确做法是**采样驱动**（`EnvSampler`），不要逐字段硬编码。
3. **"一层全骗"技术上只有 seccomp 层可行**（已具备 SIGSYS 改写 + USER_NOTIF），但应**限定 syscall 集**、并接受性能与"可被检测"的代价；**它不能替代路径规则**，只能兜底。
4. **高频**：路径翻译（1318）、`VirtualContext`（598）、沙箱目录（438/360）、启动链事件；
   **没被用上**：5 个 JNI 导出、CM 专用分支、`javaIo`（默认关）、`PropTrace`、NIO2、`nativeLoad` hook（写了没生效）。

### 立即可做的三件小事（零风险）

| 动作 | 收益 |
| --- | --- |
| 删掉/接线那 5 个未调用 JNI 导出 | 让 `check-jni-contract` 的"多余符号"提示归零，契约更干净 |
| 给 CM 专用分支加"仅 CM guest 生效"的显式注释与文档化（或收敛到开关） | 后来人不会误以为它们对普通 guest 有用 |
| 把本节 5.1/5.2 的频次做成 `tools/analyze/` 的脚本（从 `debug-trace.log` 统计） | 以后每次装机都能自动产"功能使用画像"，而不是人工 grep |
