# 架构：native 模块的关系与技术路线（2026-11-27 研究）

- 回答：`core/hook/src/main/cpp` 现在**怎么工作**、模块之间怎么连、关键过程在哪几个点、插桩怎么插。
- 实测基线：`native-hook.cpp` **8003 行**（`read` 口径，2026-11-27 复测；插桩 `fb4afc0` 后的数字，之前的 7 935 已过期）。
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
Kotlin NativeHookBridge ──JNI(24 导出)──► native-hook.cpp
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
3. `nativeGetHookStats()` → `DebugTrace.event("native", "hook stats: …")`（bind + `+5s` + `+20s` 三拍，见 §3.3）；
4. `seccomp-diag.log`、`checks.log`、`debug-trace.log` 由探针/人读。

---

## 三、关键过程与插桩设计（本轮）

### 3.1 为什么用「计数器 + JNI 取回」而不是逐次日志

- 这些 hook 在**每次 I/O** 上；逐次 `__android_log_print` 是 µs 级，会毁掉性能；
- 项目已有成熟模式：`FsOpStats`（atomic 计数）+ `FsRecord`（32 条环形）+ `nativeGetFsStats/Recent` 取回字符串 → **复用同一模式**，零新文件、零热路径日志。

### 3.2 插桩点（4 个，全部低频）

| # | 点 | 计数内容（**实际实现的字段**） | 触发频率 |
| --- | --- | --- | --- |
| **H1** | `install_hooks_callback`（每个库一次） | `libs_scanned` / `libs_patched` / `libs_skipped_self` / `libs_skipped_system` | 初始化 + 每次延迟 patch |
| **H2** | `patch_newly_loaded_libraries` | `late_passes` / `late_libs_added` | 每次新库加载 |
| **H3** | `/proc` spoof（`build_spoofed_maps` / `make_proc_spoof_fd`） | `spoof`（生成伪造 fd 的总次数，**不分 kind**）/ `maps_lines` / `maps_hidden` | 每次读 `/proc/self/*`（低频） |
| **H4** | JNI 取回 | `nativeGetHookStats()` → 一行 `key=value`（第 **24** 个导出） | bind 一次 + `+5s` / `+20s` 各一次 |

> 未实现的候选字段（`slots_patched` / `skipped_noplt` / `spoof_calls[5 种 kind]` / `maps_lines_rewritten` / `real_mode_calls`）
> 属**将来需要时再加**：当前 9 个字段已够判回归（见 `现状-native-关键过程基线.md` §三 的判据表）。

### 3.3 输出格式（可比较的基线）

```
[Dbg/native] hook stats: libs=415 patched=411 skipped_self=1 skipped_system=3 skipped_self_host=? skipped_graphics=? skipped_syslib=? late_passes=0 late_libs=0 spoof=0 maps_lines=0 maps_hidden=0
[Dbg/native] hook stats(+5s):  …同格式…
[Dbg/native] hook stats(+20s): …同格式…
```

- 三拍的来源：`bind` 时由 `VirtualApplicationManager` 的两条 bind 路径各打一次；
  `+5s`/`+20s` 由 `GuestDiagnosticsRegistration` 的 daemon 线程 `nv-native-stats` 打
  （`spoof`/`maps_*` 只在 bind **之后**才可能非 0 —— guest 真去读 `/proc/self/maps` 时）；
- 落点：`files/debug-trace.log` 的 `[Dbg/native] …` 行（规则 20：重要过程必须落盘）；
- 取法：`run-as com.nextvm.app grep -a 'Dbg/native' files/debug-trace.log`；
- 固定方式：一次真机基线 → `docs/现状/现状-native-关键过程基线.md`（字段与量级），后续每批对比
  （`libs/patched` 允许随版本漂移；`skipped_self=1`、`skipped_system=3` 是**语义判据**）。

### 3.4 契约影响

第 24 个导出 `nativeGetHookStats` **已落地**：

1. Kotlin `NativeHookBridge.nativeGetHookStats(): String` + 包装 `hookStats()`（异常返回 `n/a`）；
2. `tools/lint/check-jni-contract.mjs` 校验：Kotlin `external`=**24** / C++ `JNIEXPORT`=**29**
   （多出的 5 个是旧符号 `nativeInstall`/`nativeGetStats`/`nativeResetStats`/`nativeGetRecent`/`nativeClearRecent`，工具只提示不报错）；
3. `.so` 名与已有 23 个签名均未改。

### 3.5 明确不动

| 不动 | 原因 |
| --- | --- |
| `hooked_open` 等热路径逐次日志 | 性能 |
| JNI 已有 23 个签名、`.so` 名、`NS_*` 语义 | 契约冻结 |
| 不新增诊断文件 | 走 `debug-trace.log`（规则 20），避免 `diag_channels.tsv` 三件套 |
| `sc_is_own_apk_path` 的 CM 硬编码 | 历史妥协，登记到镜像文档「偏差登记」，不在插桩批里改 |

---
