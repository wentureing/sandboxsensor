# 代码镜像：`core/hook/src/main/cpp/core/lib_filter.{h,cpp}`

> N2-A（2026-11-27）从 `native-hook.cpp` 的 `patch_library_got` 搬出。计划/取回见
> `docs/输出/变更/20261127-N2a-lib-filter.md`；口径见 `docs/设计/设计-native重构与临时适配器.md`。

## 职责

**做什么**
- 判定一个**已加载的 .so** 要不要被 GOT patch，以及**因为哪一条判据**被跳过；
- 四级优先级（顺序即语义）：容器自身 → 图形栈 → 应用/沙箱库 → 其余系统库（由 `NS_HOOK_SYSTEM_LIBS` 决定）。

**不负责**
- ❌ ELF 解析与 GOT 写入（`patch_library_got` / `patch_got_entry` 仍在 `native-hook.cpp`）；
- ❌ 日志文本（门面保留原文案）、计数（`g_native_stats` 完全不动 —— 这是 N2-A 的硬约束）；
- ❌ `install_hooks_callback` 里的早期过滤（`libnextvm`/`linker`/`vdso`/`libart`/`libhwbinder`/CM `libDexHelper` 特例）——
  那一层另有计数与副作用（v87/v89 窗口），N2-A **一行未动**，留待 B 批。

## 函数

| 函数 | 签名 | 作用 | 前提 / 副作用 |
| --- | --- | --- | --- |
| `classify_lib` | `Verdict(const char* lib_name, bool system_libs_enabled)` | 返回 `{decision, reason}`；`system_libs_enabled` 对应 `NS_HOOK_SYSTEM_LIBS` | 纯函数；`lib_name == nullptr` 返回 `{kPatch, kNone}`（复刻原逻辑"无名字不过滤"） |
| `reason_name` | `const char* reason_name(Reason)` | 枚举 → 稳定字符串（测试/日志用） | 纯函数，字符串是契约（向量表里用同名） |

`Decision`：`kPatch` / `kSkip`；`Reason`：`kNone`(nullptr) / `kContainerSelf` / `kGraphicsStack` / `kAppLib` / `kSystemLib`。

## 偏差登记

| # | 偏差（相对 `patch_library_got` 原实现） | 类型 | 说明 |
| --- | --- | --- | --- |
| 1 | 判据从"散在函数体里的 4 个 `if`"变成一次 `classify_lib()` 调用 | ✅ 中性 | 关键词、顺序、早退位置逐字照搬；`strstr` 语义一致（同样用 `std::strstr`） |
| 2 | `NS_HOOK_SYSTEM_LIBS` 由编译期 `#if` 变成**入参** | ✅ 中性 | 门面传 `NS_HOOK_SYSTEM_LIBS != 0`；宏定义仍在门面（`#ifndef/#define` 保留） |
| 3 | 系统库开关打开时，原因仍记为 `kSystemLib` 且决策为 `kPatch` | ✅ 中性 | 与原 `#if NS_HOOK_SYSTEM_LIBS`（无日志、继续 patch）等价 |
| 4 | 日志文本全部留在门面 | ✅ 必要 | 日志是排查契约（`GOT patch skipped (container self)` 等），不能因为抽取而变；且 `core/` 不许 include `android/log.h` |

## 约束与坑

1. **不得 include** `<jni.h>` / `<android/*>` / `<link.h>` / `<sys/*>`：本文件要能被 host g++ 直接编译。
2. **判据顺序是行为**：`self` 必须在 `graphics` 之前、`graphics` 必须在 `app_lib` 之前、`app_lib` 必须在系统库判定之前。
   任何重排都等于改行为（例：宿主库在 `/data/app/` 下，若先判 `app_lib` 就会 patch 容器自己 → 死循环/宿主流被改写）。
3. **本批不动计数**：`libs_scanned`/`libs_patched`/`skipped_self`/`skipped_system` 一个字节都不改，
   所以 `现状-native-关键过程基线.md` 的 `skipped_self=1`、`skipped_system=3` 判据继续有效。
   新增 `skipped_graphics` / `skipped_relro` 与"把过滤前移"属 **N2-B**（行为变更，单独走变更流程）。
4. 图形栈判据是**事故记忆**：`libGLES*`/`libEGL*`/`eglSubDriver*`/`libvulkan*`/`vulkan.adreno*`/`libllvm-glnext*`
   六条各自都有过一次实测崩溃（RenderThread SIGABRT）；新增系统库覆盖时**不要**放宽这六条。
5. 新 `.cpp` 必须登记 `CMakeLists.txt`（否则 Android 侧 `undefined symbol`）。
6. 单测：`src/test/cpp/lib_filter_test.cpp` + `golden/lib_filter_vectors.tsv`（20 条向量 + 4 条不变量），
   不依赖其它 TU（可单独 `g++ lib_filter.cpp lib_filter_test.cpp`）。
