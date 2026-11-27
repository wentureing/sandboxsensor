# 册：native hook 与 GOT/PLT（2026-11-27）

- **时效**：2026-11-27 复核。触发复核：① hook 机制变更（换 inline hook）② `NS_*` 开关语义变更 ③ Android 大版本 ④ 容器新增 JNI
- **可信度**：A=实测（探针/日志）；B=代码级；C=推测。本文逐条标注
- **替代**：本文集中 `架构/架构-native*`（6 篇）、`架构/架构-hook*`（5 篇）、`设计/设计-native重构*`、`设计/设计-hook*`、
  `输出/记录/记录-native-hook作用域与污染.md`、`输出/调研/调研-NativeHookBridge可测试性评估.md` 等的**结论**；原文保留为证据
- 一句话：**GOT/PLT patch 只覆盖"app .so 经 PLT 的静态调用"**；`dlsym`/raw syscall/JIT 不可覆盖；运行期加载靠延迟 patch 兜底

---

## 一、现行结论

| # | 结论 | 等级 | 证据 |
| --- | --- | --- | --- |
| **N-a** | 机制 = 改写 `.rela.plt` 的 GOT 槽（`patch_got_entry`：mprotect→写槽），**不改指令** | A | 代码 + `applied/slots` |
| **N-b** | hook 表 **27 条符号**：14 条走路径翻译（open/openat/access/stat/lstat/readlink/fopen/opendir/mkdir(at)/unlink(at)/rename + 64 变体）、13 条不走（属性/dlopen/ptrace/syscall/fork/mmap/mprotect/connect/getaddrinfo） | A | `g_hook_entries` |
| **N-c** | 库过滤四级：容器自身 → 图形栈 → app/沙箱库 → 其余系统库（`NS_HOOK_SYSTEM_LIBS=0` 默认不 patch） | A | `core/lib_filter` + 20 向量 |
| **N-d** | 图形栈六条关键词（libGLES/libEGL/eglSubDriver/libvulkan/vulkan.adreno/libllvm-glnext）**永不 patch** | A | 历史两次崩溃后固化 |
| **N-e** | **`dlsym(RTLD_DEFAULT,"open")` 拿到真实地址后调用 = 绕过 hook**（原理边界） | A | `H1.dlsym_bypass`：`not-translated errno=2` |
| **N-f** | **raw `syscall` 也绕过**（`hooked_syscall` 只记日志） | A | `N09.syscall_openat` |
| **N-g** | 运行期 `System.loadLibrary` 的库，靠绑定后 +5s/+20s 触发延迟 patch 兜底；`applied=1 slots=12`（probe） | A | `现状-native-关键过程基线.md` |
| **N-h** | `patched` ≠ 真正 patch 的库数（它是"过 callback 的库数"）；`applied/slots` 才是 | A | `skipped_graphics 7 + skipped_syslib 404 = 411 = patched` |
| **N-i** | `libs/patched/skipped_*` 是**累计值**，每次延迟 patch pass 累加（`skipped_self = 1+passes`、`skipped_system = 3+3×passes`） | A | 三拍实测 |
| **N-j** | `/proc` 伪造（cmdline/comm/status/maps）**独立于路径翻译**，靠 memfd + `prctl` | A | `ProcSpoofing` + `spoof/maps_*` |
| **N-k** | Java 层 `RegisterNatives`（`Linux` 8 + `UnixFileSystem` 8）**默认关**（`javaIo=0`） | A | 日志 `SKIPPED by debug.nextvm.javaIo=0` |

---

## 二、已证伪 / 不要再试

| 路线 | 为什么 |
| --- | --- |
| 打开 `NS_HOOK_SYSTEM_LIBS=1` 覆盖更多 | 历史上导致 RenderThread SIGABRT / WebView popup 闪退；文档明确"不可谈判" |
| 靠 Java 层 hook 覆盖系统库代访问 | `File` 走 libjavacore（系统库，不 patch）；NIO 0/38 未实现 |
| 认为 `patched` 就是"打上的库数" | 见 N-h，语义偏松 |
| 把 GOT patch 当成"能拦所有文件访问" | `dlsym`/raw syscall/JIT 三条绕过（N-e/N-f） |

---

## 三、待确认 / 可能错

| # | 事项 | 现状 |
| --- | --- | --- |
| 1 | "含包名/不含包名"这类**字符串判定**在别的 ROM 是否一致 | 只在本机（vivo Android 15）验证 |
| 2 | `Runtime.nativeLoad` hook 为何从未装成功 | 无 SUCCESS 日志（`STATUS` #14 待诊断） |
| 3 | `libs=415` 是"回调次数"还是"唯一库数" | 观测上是累计回调（`415→843→1271`），未在代码里确认计数点语义 |
| 4 | seccomp 作为"一层全骗"的可行性 | 已实现 SIGSYS + USER_NOTIF，但**性能/可检测性未评估**（只作诊断） |

---

## 四、重构与观测现状

| 项 | 状态 |
| --- | --- |
| `core/path_spoof.{h,cpp}`（N1a） | ✅ 已落地（27 轮向量 + 不变量） |
| `core/lib_filter.{h,cpp}`（N2-A/B） | ✅ 已落地（20 向量 + 3 个新计数） |
| `core/proc_spoof` / `seccomp_attr` | ⏳ 未开始（N1b/N3 已降优先级） |
| 契约工具 | ✅ `check-jni-contract`（Kotlin 25 ↔ C++ 30，5 个旧符号待删） |
| 基线 | ✅ `现状-native-关键过程基线.md`（含增量判据） |

---

## 五、原始文档索引（结论以本文为准）

| 文档 | 用途 |
| --- | --- |
| `架构/架构-hook实现现状-代码为准.md` | **代码级事实**（27 符号/16 RegisterNatives/缺口清单）— 改 hook 前必读 |
| `架构/架构-native模块关系与技术路线.md` | 六层路线与插桩设计 |
| `架构/架构-多层hook该拦几层与幂等前提.md` | 该 hook 哪一层的三条判据 |
| `架构/架构-native-hook覆盖不到Java层.md` | 为什么 Java 层必须另走 RegisterNatives |
| `架构/架构-主原生guest全hook.md` | 架构主张（**建议未执行**，"21 符号砍到 14"没做） |
| `架构/架构-hook关键路径.md` / `架构-hook路径覆盖矩阵与缺口.md` | 12 种 hook 的注入点（后者的 §一 已被代码推翻） |
| `设计/设计-native重构与临时适配器.md` | 适配器三形式、性能评估、选型 |
| `现状/现状-native全景与计划台账.md` | 全景 + 计划台账（R2 状态） |
| `现状/现状-native-关键过程基线.md` | **判据表**（每批对照） |
| `输出/记录/记录-native-hook作用域与污染.md` | 污染面实测（RenderThread SIGABRT 等） |
