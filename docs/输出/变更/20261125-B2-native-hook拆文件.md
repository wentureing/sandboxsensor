# 变更：B2 拆 native-hook.cpp（按职责分 TU）

- 日期：2026-11-25
- 状态：**⏸ 暂缓**（用户 2026-11-25：先跳过含 C++ 的重构，找更容易测试的部分；计划保留，随时可恢复）
- 依据：`docs/架构/架构-C++测试夹具设计.md`、`docs/计划/计划-大文件拆包与职责导出.md` §零门禁
- 现状：单文件 **7 468 行 / 326 KB**，churn 第 1（34 次提交、1 993 行改动）

---

## 一、可用的天然边界（文件里已有分节注释）

| 行号 | 分节 | 归属 |
| --- | --- | --- |
| 92 | Global State | → 拆为 `internal/nv_state.h`（分批迁移） |
| 179 | Original Function Pointers | 同上（`real_*`） |
| 218 | Path Redirection Logic | 已是薄包装（S1 抽到 `core/path_rules`） |
| 262 | /proc/self Spoof Content | → `proc_spoof.cpp`（B2-3） |
| 900 | PropTrace（诊断） | → `diag.cpp`（B2-4） |
| 1090 | Diagnostic hooks（log-only） | → `diag.cpp`（B2-4） |
| 1504 | GOT/PLT Hook Installation | **留在** `native-hook.cpp`（装配） |
| 1543 | failed-connect / DNS ring（诊断） | → `diag.cpp`（B2-4） |
| 2267 | JNI Bridge | **留在** `native-hook.cpp`（装配） |
| 6451 | Runtime.nativeLoad Hook | → `jni_install.cpp` 或留下（B2-5） |
| 6634 | Java 层文件 I/O 重定向 | → `java_io_hooks.cpp`（B2-2） |
| 6665 / 6805 | v148 缓存引用 / 环形缓冲 | 跟随 Java IO（B2-2） |

## 二、一个必须接受的约束（决定 CI 策略）

> **改 `native-hook.cpp` 的编译错误，只有 `container-build` 能发现。**
>
> `cpp-core-test` 只编译 `core/path_rules.cpp`（纯逻辑，无 Android 头）；
> 而 `native-hook.cpp` 依赖 `jni.h`/`android/*`/`link.h`，host 无法编译（S3 已否决）。

**所以 B2 每批的验证 = 一次 `container-build`**（约 4 分钟），而不是 `module-test`。
按"一次一个 CI"纪律，每批只跑这一条。

## 三、状态访问层（B2-0，唯一需要先设计的东西）

74 个 `static` 全局变量分两类：

| 类别 | 例子 | 处理 |
| --- | --- | --- |
| 被多个模块共享 | `g_path_redirects`、`g_hidden_paths`、`g_spoofed_package_name`、`g_spoofed_pid`、`real_*` 指针、`g_hooked_java_*` 缓存 | → `internal/nv_state.h` 的 **accessor 函数返回引用**（`RuleTable& path_rules()`），消除静态初始化顺序问题 |
| 单模块私有 | 各统计计数器、v8x 实验窗口 flag、环形缓冲 | 跟随所属模块走，**留在该模块 .cpp 的匿名命名空间**，不进头 |

**规则**：
1. `internal/nv_log.h` 逐字搬运 `LOG_TAG` + `LOGI/D/W/E`（debug 面不得变化）；
2. `internal/nv_jni.h` 只放 jstring↔string、抛 `ErrnoException` 这类**无状态**辅助；
3. **模块之间禁止互相 include**，只允许 include `internal/`；
4. 每批只把"该批模块用到的状态"迁成 accessor，不一次性动 74 个。

## 四、分批（每批一个职责，一次 container-build）

| 批 | 内容 | 风险 | 回退 |
| --- | --- | --- | --- |
| **B2-0** | `internal/nv_log.h` + `nv_jni.h` + `nv_state.h`（**只新增，不改调用点**） | 极低（0 行为变化） | 删文件 |
| **B2-1** | 路径/文件操作 hook（`hooked_open/openat/access/stat/lstat/readlink/fopen/opendir/mkdir/mkdirat/unlink/unlinkat/rename`）→ `path_redirect.cpp` | 中（热路径） | `git revert` |
| **B2-2** | Java 层 IO（`install_java_io_hooks` + `hooked_java_*` + `hooked_ufs_*`）→ `java_io_hooks.cpp` | 中（A/B 族语义） | 同上 |
| **B2-3** | `/proc/self` 伪造 + 属性（`spoof*`、maps）→ `proc_spoof.cpp` | 中 | 同上 |
| **B2-4** | 诊断（PropTrace、fs 统计、seccomp、failed-connect ring）→ `diag.cpp` | 低 | 同上 |
| **B2-5** | 装配收尾：`native-hook.cpp` 只剩 GOT patch 过滤 + JNI 注册 + 生命周期 | 低 | 同上 |

## 五、debug 面保护（每批必查）

| # | 面 | 判据 |
| --- | --- | --- |
| 1 | 日志宏 | `nv_log.h` 与拆分前 `#define LOGI/LOGD/LOGW/LOGE` + `LOG_TAG` **逐字相同**（脚本 diff） |
| 2 | fs 统计 | `nativeGetFsStats()` 输出格式不变（`open=%d/%d/%d ...`） |
| 3 | 路径日志 | 真机 `Dbg/path` 与 `open: A -> B` 仍在 |
| 4 | 诊断过滤 | `is_container_diag_path()` 保留（`438eab2`） |
| 5 | 归因 | `diag_caller` 的调用方归因可能因跨 TU 内联变化 → 真机对比一条 `open:` 日志的 caller 字段 |
| 6 | 渲染/popup | 库过滤逻辑未动 → 期望不变（真机 smoke） |

## 六、验收

| 项 | 判据 |
| --- | --- |
| 编译 | `container-build` 绿（每批唯一能验证 C++ 的手段） |
| 纯逻辑 | `cpp-core-test` 仍绿（16 向量） |
| 行为 | 装包 smoke 8 项（见 `20261125-B1-NativeHookBridge拆包.md` §七） |
| 行数 | 每批后 `native-hook.cpp` 单调下降；最终只剩装配（目标 < 2 000 行） |

## 七、暂不做

| 项 | 原因 |
| --- | --- |
| 宿主桩（自制 `jni.h`/`android/log.h`） | 测的是桩，不是 Android（S3 已否决） |
| 依赖注入（S2） | 需要改 `hooked_*` 签名；等 B2-1/B2-2 落地后再评估 |
| 设备内自检（S4） | 用户已暂停 |
