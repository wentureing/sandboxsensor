# 现状：native 全景与计划台账（2026-11-27 整理）

- 回答：**native 这块现在是什么样、文档里都写了什么、计划执行到哪一步、下一步按什么顺序做。**
- 用途：接手 / 改 native 之前只读这一篇就能定方向；细节仍回原文（见 §六）。
- 口径：行数用 `read` 口径；符号/导出用 `grep JNIEXPORT` 与 `tools/lint/check-jni-contract.mjs` 实测。
- 关联：`架构-native模块关系与技术路线.md`、`架构-hook实现现状-代码为准.md`、`设计-native重构与临时适配器.md`、

---

## 一、代码事实（今天核对，非引用旧文档）

| 项 | 实测值 | 说明 |
| --- | --- | --- |
| `native-hook.cpp` | **7886 行** | N1a 搬出 117 行（改前 8003）；更早文档写的 7935 是 `fb4afc0` 插桩前口径 |
| `core/` 下已有纯逻辑 | `path_rules.{h,cpp}`(27/43)、`path_pattern.{h,cpp}`(33/52)、`path_spoof.{h,cpp}`(49/140)、**`lib_filter.{h,cpp}`(33/54)**、`diag_channels.inc`(31) | `proc_spoof.*` / `seccomp_attr.*` **不存在** → N1b/N3 未开始；**N1a 与 N2-A 已落地** |
| `CMakeLists.txt` 源列表 | `native-hook.cpp` + `core/path_rules.cpp` + `core/path_pattern.cpp` | 新 `.cpp` 必须登记，否则 `undefined symbol` |
| 编译选项 | `-O2`、`-fno-exceptions`、`-fno-rtti`、visibility `hidden`、`-Wl,-z,max-page-size=16384`；**无 LTO** | 决定适配器开销（跨 TU ≈1–3 ns/次） |
| `JNIEXPORT` 函数 | **29 个** | 其中 **24 个**与 Kotlin `external fun` 一一对应，另 5 个是旧符号（`nativeGetRecent`/`nativeClearRecent`/`nativeGetStats`/`nativeResetStats`/`nativeInstall`） |
| JNI 契约 | `check-jni-contract.mjs` → **24 ↔ 29**，`JNI_OnLoad` 不存在（`loadLibrary` + `nativeInit` 设计） | 工具只提示多余符号，不报错 |
| C++ host 测试 | `path_rules_test.cpp`、`path_pattern_test.cpp` + `src/test/resources/golden/path_vectors.tsv` | CI job `cpp-core-test`（ubuntu 自带 g++，**不新增 workflow**） |
| 开关默认值 | `NS_HOOK_SYSTEM_LIBS=0`（编译期，不 patch 系统库）、`debug.nextvm.javaIo`=**false**、`javaIoTranslate`=true | 决定"默认生效面"（见 §三） |

---

## 二、技术路线（六层，来自 `架构-native模块关系与技术路线.md`）

> **用 GOT/PLT patch 把 libc 的 I/O 入口换成自己的实现，在实现里做路径翻译、`/proc` 伪造与诊断统计；不 hook 系统调用本身（除 seccomp 诊断）。**

| 层 | 入口 | 机制 |
| --- | --- | --- |
| ① 加载 | `System.loadLibrary("nextvm-native")` → `nativeInit` | `install_plt_hooks()` |
| ② 解析 | `install_plt_hooks` | `dlsym(RTLD_DEFAULT, …)` 存 20+ 个 `real_*` 原函数指针 |
| ③ patch | `install_hooks_callback` | `dl_iterate_phdr` 遍历 ELF → `.rela.plt` → 写 GOT 槽 |
| ④ 延迟 patch | `patch_newly_loaded_libraries` | 新库 dlopen 后重跑；`g_patched_bases` 保证幂等 |
| ⑤ 行为 | `hooked_open/openat/access/stat/lstat/readlink/fopen/opendir/mkdir(at)/unlink(at)/rename` | 先 `redirect_path` 翻译 → 未命中走原函数；`/proc/self/*` → 伪造内容 memfd |
| ⑥ 诊断 | `FsOpStats` + `FsRecord`、seccomp `sc_write_diag`、`is_container_diag_path` | 原子计数 + 环形记录，JNI 取回字符串；seccomp 写 `seccomp-diag.log` |

**符号面**（`架构-hook实现现状-代码为准.md` §二，2026-11-25 代码核对）：**27 条符号**
= 13 个函数体 / 14 个符号**走**路径翻译（含本仓库新增的 5 条写操作 `mkdir`/`mkdirat`/`unlink`/`unlinkat`/`rename`）
\+ 13 个**不走**翻译（`__system_property_get`、`dlopen`/`android_dlopen_ext`/`dl_iterate_phdr`、`ptrace`/`syscall`/`fork`、`mmap`/`mprotect`、`connect`/`getaddrinfo`）。

---

## 三、当前生效面与已知缺口

### 3.1 库级四级过滤（`patch_library_got` 的过滤链）

| 优先级 | 类别 | 处置 | 理由 |
| --- | --- | --- | --- |
| 1 | 容器自身（`com.nextvm.app` / `libnextvm`） | 永不 patch | 拦自己会死循环/错改宿主 IO |
| 2 | 图形栈（`libGLES*`/`libEGL*`/`eglSubDriver*`/`libvulkan*`/`vulkan.adreno*`/`libllvm-glnext*`） | 永不 patch | 实测 patch 后 shader 编译 abort（RenderThread SIGABRT） |
| 3 | 应用/沙箱库（`/data/app/`、`/data/user/`、`/virtual/`） | 始终 patch | 多数 guest 自己的 native 代码 |
| 4 | 其余系统库 | `NS_HOOK_SYSTEM_LIBS` 默认 **0=不 patch** | 系统库运行前提不能改坏 |

不可谈判：**不要为了"覆盖更多"打开 `NS_HOOK_SYSTEM_LIBS=1`**，要覆盖某个系统库走逐个精确白名单并记理由。

### 3.2 默认配置下是「双层不一致」（这条决定很多现象的解释）

```
默认（NS_HOOK_SYSTEM_LIBS=0, javaIo=false）
  app 自带 .so 直接调 libc        → GOT 被 patch            → 落沙箱 ✓
  Java 层 File / FileInputStream  → libjavacore 未 patch，
                                    且 RegisterNatives 未装 → 落宿主真实路径 ✗
```

- `File` 是**纯 Java**（无 native 方法）且**分叉**：`list0/createDirectory0/...` 走 `UnixFileSystem`，
  `length/delete/renameTo` 绕去 `Linux.*` → **必须两层都 hook**；`FileSystem` 这条链 hook 不到。
- 这解释了"宿主出现空目录壳 + 沙箱里有文件"的结构差。
- Java 层覆盖（`RegisterNatives`，**16 个方法，默认不安装**）：`libcore.io.Linux` 8 个 + `java.io.UnixFileSystem` 8 个。
  **语义等价是硬要求**：A 族失败抛 `ErrnoException`、B 族返回 `null/false/0`；v141 只对齐成功路径 → Via 完全不可交互。

### 3.3 已知缺口（`架构-hook实现现状-代码为准.md` §六，判据：落宿主 / guest 功能异常 / 不可接受泄露）

| # | 缺口 | 状态 |
| --- | --- | --- |
| 1 | NIO2 `sun.nio.fs.UnixNativeDispatcher` 0/38 | ⏸ 暂不实现（用户决定） |
| 2 | A 族 `readlink` 未 hook → 返回真实目标 | 🔶 疑似缺陷（泄露） |
| 3 | `setPermission0` 未 hook | 🔶 待评估 |
| 4 | `getSpace0`/`statvfs`/`fstatvfs`、`chown`/`link`/`symlink`/`mknod0` | ✅ 设计取舍 |
| 5 | **L6 运行期加载库**（`libnativeloader.so` 未 patch → 延迟 patch 不触发） | ⚠️ 待修（成本高） |
| 6 | **L7 直接 syscall**（`hooked_syscall` 只打日志） | ✅ 设计取舍（靠伪造数据源兜底） |
| 7 | `hooked_open` 无 `ENOENT` 兜底 | 🔶 待评估（与层间不一致同源） |
| 8 | `hiddenPaths` 无 native 精确匹配 | ⚠️ 待修（低优先） |

---

## 四、插桩与基线（本轮已在真机复验）

### 4.1 设计要点

- **不逐次打日志**：hot path 只做 `std::atomic` 加一；取值走 JNI `nativeGetHookStats()` → `DebugTrace.event("native", …)` → `files/debug-trace.log` 的 `[Dbg/native]` 行（规则 20）。
- 采样三阶段：bind（`VirtualApplicationManager` 两条 bind 路径各一行）+ **+5s / +20s**（`GuestDiagnosticsRegistration` 的 daemon 线程 `nv-native-stats`）。
- 存在的理由：`spoof/maps_*` 发生在 bind **之后**（guest 真去读 `/proc/self/maps` 时），bind 那一拍必然是 0。

### 4.2 基线（`现状-native-关键过程基线.md`，干净安装实测）

| 字段 | 基线值 | 判据 |
| --- | --- | --- |
| `libs` / `patched` | 415 / 411 | 只记量级（随 ROM/版本漂移） |
| `skipped_self` | **1** | **必须保持 1**（只跳 `libnextvm-native.so`） |
| `skipped_system` | **3** | **必须保持 3**（linker / vdso / libart / libhwbinder 固定命中 3） |
| 恒等式 | `patched = libs − skipped_self − skipped_system` | 不等说明有未统计的早退分支（插桩缺口） |
| `late_passes`/`late_libs` | 0 / 0 | 非 0 = 运行期 dlopen（正常） |
| `spoof`/`maps_lines`/`maps_hidden` | 0 / 0 / 0 | **有解释**：本轮没有"经 libc 读 `/proc` 的 native 读者" |

**本轮新增的实证（探针 `2e7e675`）**：`N16.libc_maps` = `lines=3252 pkg_hit=18`，与 `N10`（`fopen` 路径）`pkg_hit=18` 一致
→ 经 libc `open/read` 读 maps 这条 PLT 路径确实被计数；`[Dbg/native]` 的 `spoof/maps_lines` 仍为 0 的解释继续成立。

---

## 五、计划台账（做什么、到哪了）

### 5.1 总表（`计划-重构阶段任务总表.md` §R2）

| 批 | 内容 | 状态 |
| --- | --- | --- |
| **R2-0** | 契约工具 `check-jni-contract.mjs` | ✅ **已落地**（工具存在、当前 24↔29 通过）；总表里写的"23 ↔ 23"已过时 |
| **R2-1 = N1a** | ① 路径/映射合成 ② 等价族兜底 → `core/path_spoof.{h,cpp}` | ✅ **已落地**：代码 + 33 条向量 + 5 条不变量 + 镜像文档（`20261127-N1a-path-spoof.md`）；CI 侧修了两轮（链接缺 `path_pattern.cpp`、`(deleted)` 期望值），见该文 §八 |
| **R2-2 = N1b/N2** | ① maps 行过滤/改写 ② `status`/`stat` 生成 → `core/proc_spoof.{h,cpp}` | ⏳ 未开始 |
| **R2-3 = N3** | seccomp 归因 → `core/seccomp_attr.{h,cpp}` | ⏳ 未开始 |
| **R2-4 = N4** | 库过滤判定表 → `core/lib_filter.{h,cpp}` | 🟡 **N2-A 已落地**（判据表 + 20 向量 + 4 不变量，计数/日志不变）；N2-B（新增计数 + 过滤前移）待 A 绿后单独做 |

同一篇总表的 **D2 目标**：`native-hook.cpp` **≤ 5 200 行**（N1a 后 7886 → 还差 2 686 行，A/B/C 三类纯逻辑约 1 500–2 000 行可下沉）。

> **R3 先行一项（已修，2026-11-27）**：`(deleted)` 后缀剥离 11 → 10 —— 上游 fork 前遗留（`a1871a6`/`e590c32`，woand）。
> 修法把长度提成 `kDeletedSuffixLen`（`sizeof - 1` 派生，`path_spoof.h` 与 `native-hook.cpp` 的 `sc_rewrite_fd_target` 共用），
> 加 3 条向量 + 3 条不变量 + CI 静态守卫（`tools/analyze/verify-deleted-suffix.mjs --strict`）。
> 见 `docs/输出/变更/20261127-deleted-suffix-11to10.md`。**这是"抽纯逻辑 + 写向量"逼出来的历史缺陷**（此前零触发）。

### 5.2 N1a 的完整计划（`计划-native-N1a-path-spoof.md`，已就绪待执行）

| 项 | 内容 |
| --- | --- |
| 判据 | "不为割而割"四问：割完能测什么 / 能改什么 / 契约是否只需同名转发 / 风险是否可控 |
| 范围（两个职责） | ① `synthetic_apk_path` / `synthetic_lib_path` / `rewrite_virtual_path` / `maps_line_visible` / `transform_maps_line` ② `redirect_path` 的 `/data/user/0`↔`/data/data` 等价族兜底 |
| 核心收益 | **`guest_pkg` 从全局 `g_spoofed_package_name` 变成入参** → 多实例 / 多 guest 时不再依赖全局状态 |
| 新文件 | `core/path_spoof.{h,cpp}`、`src/test/cpp/path_spoof_test.cpp`、`golden/path_spoof_vectors.tsv` |
| 契约 | **形式 A 同名转发**（门面 = `return core::…`），调用点一行不改；台账 5 行，N1b/收尾删除 |
| 验收 | `cpp-core-test` + golden 向量 + 微基准 <5% + `check-jni-contract` 24↔24 + 真机 N03–N09/W36 + 镜像文档四节 |
| 明确不做 | 不搬 `build_spoofed_maps/status/stat` 的**读文件部分**（N1b 拆"读 + 转"）；不动 `hooked_*`、不动 JNI 签名、不改 `maps_line_visible` 关键词表、不引入 doctest |

### 5.3 native 重构的三条硬边界（`设计-native重构与临时适配器.md`）

| 允许 | 不允许 |
| --- | --- |
| 只搬 **A 路径/映射合成、B `/proc` 内容伪造、C seccomp 归因** 三类纯逻辑 | 搬 **E 类 hook 入口**（`hooked_open/stat/…`）、**F 类 JNI/`JNI_OnLoad`** |
| 临时适配器：**A 同名转发**、**B `extern "C"` 兼容符号** | **C 宏/内联切换进 release** |
| 新文件不 include JNI/Android 头，host g++ 可测 | 新增堆分配 / 锁 / 热路径日志 / 异常（`-fno-exceptions` 下 `throw` 直接编译失败） |
| 每批带 golden 向量 + 微基准（增幅 **<5%**） | 改 **JNI 名/签名、`.so` 名、`NS_*` 语义** |
| 适配器台账每加一行（文件:符号 / 形式 / 为什么 / 何时删 / 怎么验证） | 适配器留到 release（R4 门禁：`TODO(adapter:` = 0） |

性能结论：跨 TU 同名转发 ≈ **1–3 ns/次**，相对一次 `openat`（≈1–2 µs）与 JNI 调用（≈10+ µs）可忽略；
**真正的风险是抽取时改数据流**（新增 `std::string` 拷贝、加锁、热路径日志），不是转发本身。

---

## 六、文档地图（该读哪篇、哪篇别当依据）

| 文档 | 回答什么 | 状态 |
| --- | --- | --- |
| `架构/架构-native模块关系与技术路线.md` | 六层路线 + 模块调用链 + 插桩点设计 | ✅ 当前（**§3.2/§3.3 与实际实现不符，见 §七**） |
| `架构/架构-hook实现现状-代码为准.md` | **代码级事实**：27 符号、16 个 RegisterNatives、四级过滤、缺口清单 | ✅ 当前，**改 hook 前必读** |
| `架构/架构-C++测试夹具设计.md` | 为什么不能整文件 host 编、S1 抽纯逻辑、向量单一来源 | ✅ 当前（S1 已落地；**S4 设备自检暂停**） |
| `设计/设计-native重构与临时适配器.md` | 怎么拆、三种适配器、性能评估、测试框架与 hook 库选型 | ✅ 当前 |
| `现状/现状-native-关键过程基线.md` | 每批重构要对照的字段与量级 | ✅ 当前（本轮补了 N16 实证） |
| `计划/计划-native-N1a-path-spoof.md` | N1a 的范围/签名/适配器/golden/验收 | ✅ 已按计划执行（见 `20261127-N1a-path-spoof.md`） |
| `计划/计划-重构阶段任务总表.md` | R1–R4 与 DoD（含 D2 ≤5 200 行） | ⚠️ R2-0 的"23↔23"已过时 |
| `架构/架构-native-hook覆盖不到Java层.md` | 为什么 Java 层必须另走 `RegisterNatives` | ✅ 原理仍成立 |
| `架构/架构-多层hook该拦几层与幂等前提.md` | 该 hook 哪一层的三条判据 + 翻译幂等已实测 | ✅ 仍成立（新增规则必须重新验幂等） |
| `代码镜像/core/hook/ProcSpoofing.md` | `/proc` 伪造的职责与偏差（含"可临时关闭"的必要偏差） | ✅ 当前 |
| `架构/架构-hook关键路径.md` | 12 种 hook 的注入点/生效范围/失效条件/观测点 | ✅ 术语来源（L4 结论已被代码取代） |
| `架构/架构-hook路径覆盖矩阵与缺口.md` | 方案A 实测结论 | ⚠️ **§一 矩阵已被代码推翻**，只保留 §二/§三 |
| `架构/架构-主原生guest全hook.md` | "主进程零 hook / guest 全 hook" 的架构主张 | ⚠️ **是建议、未执行**（"21 符号砍到 14"没做，实际 27） |
| `架构/架构-为什么native在UnixFileSystem与跨版本兼容.md` | 为什么 native 方法在那些类里、怎么跨版本 | ⚠️ §2.2"L3 用 RegisterNatives 覆盖 NIO"已作废（NIO **0 实现**） |
| `现状/现状-hook验证手段矩阵.md` | 各级 hook 能怎么验证、原项目测试完善程度（0.54%） | ⚠️ 部分已被"自检 8 项 + 探针 100 项"补上 |
| `输出/记录/记录-native-hook作用域与污染.md` | 污染面实测（RenderThread SIGABRT 等） | ✅ 证据 |
| `输出/变更/20261125-B2-native-hook拆文件.md` | B2 拆文件的实际动作 | ✅ 历史 |

---

## 七、文档与代码不一致（2026-11-27 已对齐 1–4，第 5 条保留为注记）

| # | 位置 | 文档说法 | 代码实测 | 处置 |
| --- | --- | --- | --- | --- |
| 1 | `架构-native模块关系与技术路线.md` §3.3 | 输出三行（`hook:` / `late:` / `proc:`） | 实际 **一行** `libs=/patched=/skipped_self=/skipped_system=/late_passes=/late_libs=/spoof=/maps_lines=/maps_hidden=` + `+5s`/`+20s` 两拍 | ✅ 已改文档对齐代码 |
| 2 | 同上 §3.2 | 字段含 `slots_patched`、`spoof_calls[5 种 kind]`、`maps_lines_rewritten`、`real_mode_calls`、`skipped_noplt` | **未实现**（只有 self/system 两个 skip 原因） | ✅ 已改文档，并写明"将来需要再加" |
| 3 | 同上 §一 / `STATUS.md` | `native-hook.cpp` 7935 行 | 8003 行（N1a 后 **7886**） | ✅ 已更新 |
| 4 | `计划-重构阶段任务总表.md` R2-0 | "23 导出 ↔ 23 `external fun`" | **24 ↔ 29**（5 个旧符号） | ✅ 已更新为 24↔29 并标"已落地" |
| 5 | `架构-C++测试夹具设计.md` §3.1 | 表格把 `is_path_hidden` 列为"可从 native 抽" | 已在 `nextvm::path`，门面只剩薄壳 | 注记已补（不必再抽） |

---

## 八、下一步建议（顺序与门禁）

| 顺 | 动作 | 为什么排这里 |
| --- | --- | --- |
| 0 | ~~文档对齐~~ / ~~**N1a**~~ / ~~`(deleted)` 修复~~ | ✅ 均已完成 |
| 1 | **N1b**（`build_maps_content(pkg, real_content)` / `build_status_content` / `build_stat_content`） | 把 `/proc` 生成的"读文件"与"转换"分开，才可 host 测；N1a 样板已就位 |
| 2 | N3 seccomp 归因 / N4 库过滤表 | 纯表 + 判定，改动面小 |
| 3 | L6 运行期加载库（`libnativeloader.so`） | 唯一影响真实隔离效果的缺口，但成本高，放纯逻辑重构之后 |

**每批门禁**（不变）：T0 静态（`check-jni-contract` / `check-diag-channels` / `kotlin-lint`）
→ T1 等价或微基准（<5%）→ T2/T3 一次 `module-test` → T4 真机 5 项 + 探针 N 系列 → T5 镜像文档四节。
**对照基线**：`skipped_self=1` / `skipped_system=3` 不许变，`libs/patched` 只记量级。
