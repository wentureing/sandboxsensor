# 架构：native 模块的关系与技术路线（2026-11-27 研究）

- 回答：`core/hook/src/main/cpp` 现在**怎么工作**、模块之间怎么连、关键过程在哪几个点、插桩怎么插。
- 实测基线：`native-hook.cpp` **7935 行**（`read` 口径；`Measure-Object -Line` 统计 7471 是少算末行，以 7935 为准）。
- 关联：`设计-native重构与临时适配器.md`、`架构-hook实现现状-代码为准.md`、`架构-C++测试夹具设计.md`

---

## 一、技术路线（一句话 + 六层）

> **用 GOT/PLT patch 把 libc 的 I/O 入口换成自己的实现，在实现里做路径翻译、`/proc` 伪造与诊断统计；不 hook 系统调用本身（除 seccomp 诊断）。**

| 层 | 入口 | 机制 | 关键代码 |
| --- | --- | --- | --- |
| ① 加载 | `System.loadLibrary("nextvm-native")` → `nativeInit` | `install_plt_hooks()` | `:2278` |
| ② 解析 | `install_plt_hooks` | `dlsym(RTLD_DEFAULT, …)` 保存 20+ 个 `real_*` 原函数指针 | `:2169-2235` |
| ③ patch | `install_hooks_callback` | `dl_iterate_phdr` 遍历 ELF → 解析 `.rela.plt` → 写 GOT 槽（`patch_got_entry`） | `:1756` / `:2169` |
| ④ 延迟 patch | `patch_newly_loaded_libraries` | 新库 dlopen 后重跑，`g_patched_bases` 幂等 | `:2248` |
| ⑤ 行为 | `hooked_open/openat/access/stat/lstat/readlink/fopen/mkdir(at)/unlink(at)/rename/opendir/…` | 路径翻译（`redirect_path`）→ 未命中走原函数；`/proc/self/*` → 伪造内容 memfd | `:649` / `:238` / `:585` |
| ⑥ 诊断 | `FsOpStats` + `FsRecord`、seccomp `sc_write_diag`、`is_container_diag_path` | 计数器 + 环形记录，经 JNI 取回字符串；seccomp 写 `seccomp-diag.log` | `:1298-1350` / `:2715` / `:7153` |

### 已"下沉"的纯逻辑（可直接 host 测）

| 文件 | 职责 | 测试 |
| --- | --- | --- |
| `core/path_rules.{h,cpp}` | 路径隐藏判定 / 前缀重定向（`nextvm::path::*`） | `path_rules_test.cpp` |
| `core/path_pattern.{h,cpp}` | 路径模式匹配 | `path_pattern_test.cpp` |
| `core/diag_channels.inc` | 诊断文件白名单（由 `diag_channels.tsv` 生成） | `check-diag-channels.mjs` |

---

## 二、模块关系（调用链）

```
Kotlin NativeHookBridge ──JNI(23 导出)──► native-hook.cpp
   ▲                                        │
   │                                        ├─ nextvm::path::*（core/path_rules）
   │                                        ├─ nextvm::diag::is_container_diag_path（core/diag_channels.inc）
   │                                        └─ libc（real_* 原指针）
   │
调用方：
  VirtualEngine.initNativeHooks()        → nativeInit、规则注册、setProcessName、spoofProcSelf
  VirtualEngine.ensureGuestApplicationBooted() → setArgV0 之后的 setProcessName
  VirtualApplicationManager.bindApplication()  → nativeLoad hook、javaIo stats/recent（DebugTrace）
  JavaIoHooks.stats()/recent()           → nativeGetFsStats/nativeGetFsRecent
  GuestSelfCheck / 探针                   → 读取 diag 文件
```

**当前诊断已被消费的三条线**：
1. `nativeGetFsStats()` → `JavaIoHooks.stats()` → `DebugTrace.event("javaIo", "stats after bind: …")`（`bindApplication` 里两处）；
2. `nativeGetFsRecent()` → 同上（"recent opens after bind"）；
3. `seccomp-diag.log`、`checks.log`、`debug-trace.log` 由探针/人读。

---

## 三、关键过程与插桩设计（本轮）

### 3.1 为什么用「计数器 + JNI 取回」而不是逐次日志

- 这些 hook 在**每次 I/O** 上；逐次 `__android_log_print` 是 µs 级，会毁掉性能；
- 项目已有成熟模式：`FsOpStats`（atomic 计数）+ `FsRecord`（32 条环形）+ `nativeGetFsStats/Recent` 取回字符串 → **复用同一模式**，零新文件、零热路径日志。

### 3.2 插桩点（4 个，全部低频）

| # | 点 | 计数内容 | 触发频率 |
| --- | --- | --- | --- |
| **H1** | `install_hooks_callback` 累计（每个库一次） | `libs_scanned` / `libs_patched` / `libs_skipped`（原因计数：self/system/no-plt/not-elf）/ `slots_patched` | 初始化 + 每次延迟 patch |
| **H2** | `patch_newly_loaded_libraries` | `late_passes` / `late_libs_added` / `late_slots_added` | 每次新库加载 |
| **H3** | `/proc` spoof（`build_spoofed_maps` 等） | `spoof_calls[5 种 kind]` / `maps_lines_total` / `maps_lines_hidden` / `maps_lines_rewritten` / `real_mode_calls` | 每次读 `/proc/self/*`（低频） |
| **H4** | JNI 取回 | `nativeGetHookStats()` → 一行 `key=value` 字符串 | 由 `bindApplication` 调用一次/绑定 |

### 3.3 输出格式（可比较的基线）

```
hook: libs=41 patched=40 skipped_self=1 skipped_system=0 skipped_noplt=0 slots=187
late: passes=3 libs=2 slots=9
proc: cmdline=1 maps=2 comm=1 status=1 stat=1 lines=820 hidden=0 rewritten=5 real=0
```

- 落点：`files/debug-trace.log` 的 `[Dbg/native] …` 行（规则 20：重要过程必须落盘）；
- 取法：`run-as com.nextvm.app grep -a 'Dbg/native' files/debug-trace.log`；
- 固定方式：一次真机基线 → `docs/现状/现状-native-关键过程基线.md`（字段与量级），后续每批对比（`libs/slots` 允许随版本漂移，`skipped_*` 与 `hidden/real` 是语义判据）。

### 3.4 契约影响

新增 1 个 JNI 导出（23 → **24**）→ 必须：
1. Kotlin `NativeHookBridge` 加 `external fun nativeGetHookStats(): String` + 包装 `hookStats()`；
2. `tools/lint/check-jni-contract.mjs`（R2-0）校验 24 ↔ 24；
3. 不改 `.so` 名、不改已有 23 个签名。

### 3.5 明确不动

| 不动 | 原因 |
| --- | --- |
| `hooked_open` 等热路径逐次日志 | 性能 |
| JNI 已有 23 个签名、`.so` 名、`NS_*` 语义 | 契约冻结 |
| 不新增诊断文件 | 走 `debug-trace.log`（规则 20），避免 `diag_channels.tsv` 三件套 |
| `sc_is_own_apk_path` 的 CM 硬编码 | 历史妥协，登记到镜像文档「偏差登记」，不在插桩批里改 |

---
