# 调研：debug 模块的独立性与代码重复度（含优化计划，**不执行**）

- 日期：2026-11-25
- 基线：`baseline-v150` @ `3797bad`
- 目的：用户要求"调查当前 debug 模块开发的独立性和代码重复程度，计划优化，不执行"
- **本文只做计划，不动代码**

---

## 一、模块盘点（14 个文件 / 2580 行）

| 模块 | 行数 | 位置 | 职责 | 独立性 |
| --- | --- | --- | --- | --- |
| `DebugFlags` | 117 | `common` | 开关统一源（系统属性 + setprop） | **高** ✅ |
| `DebugTrace` | 156 | `common` | 事件/detail/fail + **同步**文件日志 | 中 ⚠️ |
| `GuestBreadcrumb` | 135 | `virtualization/lifecycle` | guest 生命周期有序记录（199 处埋点） | 中 ⚠️ |
| `CheckResult` / `Diagnostics` / `DiagnosticsRouter` / `FileSink` / `DiagnosticsHolder` | 286 | `common/diagnostics` | 结构化验证序列（check/error/stage）+ 异步落盘 | **高** ✅ |
| `StateProbe` | 553 | `lifecycle` | 状态快照 | **低** ❌ |
| `DexMemDump` | 237 | `lifecycle` | dex 内存 dump | **低** ❌ |
| `WebViewWatch` | 170 | `lifecycle` | WebView 绘制/线程观察 | **低** ❌ |
| `ShortcutProbe` | 187 | `proxy` | 快捷方式探测 | 中 |
| `diag/DiagnosticsSink` | 78 | `virtualization/diag` | "测试挂钩"接口 | **死代码** ❌ |
| `FsOpStats`（native） | ~90 | `native-hook.cpp` | fs hook 计数 + 环形缓冲 | **高**，但**与 java_io 重复** ⚠️ |

---

## 二、重复度：三处实质重复

### 重复 1：**两个同名 `DiagnosticsSink` 接口**（最严重）

| 项 | 包 | 方法 | 引用 |
| --- | --- | --- | --- |
| **上游版** | `com.nextvm.core.virtualization.diag` | `onEvent(phase, detail)` / `onSnapshot(name, value)` | **0 处** |
| **重构版** | `com.nextvm.core.common.diagnostics`（嵌在 `Diagnostics.kt`） | `onCheck` / `onError` / `onStage` | 6 处 |

**问题**：
1. **同名不同包** → 读代码时先要确认是哪个
2. 上游版**从未接入**（其 KDoc 写"接入方式（P1-1）：独立模块 `:core:diagnostics` 提供真实实现"）→ **计划未落地**
3. 两套的语义**不同**：上游是"事件+快照"，重构版是"验证序列" —— **不是简单重命名能合并的**

**根因**：重构时新建了 `diagnostics/`，没有先查有没有既有的 `diag/`。

### 重复 2：**文件日志实现散落 40+ 处**

直接 `FileOutputStream` / `appendText` / `writeText` 的位置（按文件统计）：

| 文件 | 次数 |
| --- | --- |
| `VirtualApplicationManager.kt` | **12** |
| `NativeHookBridge.kt` | 5 |
| `ActivityThreadHook.kt` | 2 |
| `VirtualEngine.kt` | 2 |
| `StateProbe.kt` | 2 |
| `ActivityManagerProxy.kt` | 3 |
| `DebugTrace` / `FileSink` / `GuestBreadcrumb` / `DexMemDump` / `SandboxManager` / `ShortcutProbe` / `ActivityBinderWrap` / `JavaIoHooks` | 各 1–2 |

**两套已封装的**：
- `DebugTrace.FileLogTree`：**同步**（全局锁 + `file.length()` stat + open/write/close）≈ 0.15–1 ms/条
- `diagnostics/FileSink`：**异步**（200ms / 64 行批量）

**问题**：
1. **同步 vs 异步并存** → 谁在热路径上用同步的，就是隐患（`handleMessage` 的 per-message detail 已记录 ≈50ms/s）
2. `VirtualApplicationManager` 里 12 处**裸写文件** → 每处都要自己处理目录/异常/编码
3. 没有统一的"日志文件清单"，排查时不知道该 cat 哪个

### 重复 3：**native 统计设施两套同构**

| 项 | `g_java_io_*`（v146） | `g_fs_*`（本轮） |
| --- | --- | --- |
| 计数 | `total` / `fail` / `translated` | `total` / `translated` / `fail` |
| 环形缓冲 | `JAVA_IO_RECENT_CAP = 32` | `FS_RECENT_CAP = 32` |
| 记录条件 | 失败 / 发生翻译 | 同 |
| JNI 暴露 | `nativeGetStats` / `nativeGetRecent` / `nativeResetStats` | `nativeGetFsStats` / `nativeGetFsRecent` / `nativeResetFsStats` |
| 日志限流 | `g_java_io_open_logs` 前 N 条 | `g_fs_logs` 前 24 条 |

**结构完全同构，只是操作名不同** → 可以合成一个 `OpStats` + 一个 `recent` 表。

---

## 三、独立性评估：哪些模块能单独开关/单独测试

| 能力 | 能否独立关闭 | 能否独立测试 | 说明 |
| --- | --- | --- | --- |
| `DebugFlags` 各开关 | ✅ | ✅ | 唯一开关源，设计正确 |
| `diagnostics/`（check/error/stage） | ✅（`DiagnosticsHolder` 未 install 即 NoOp） | ✅ 已有 3 个纯 JVM 测试 | **独立性最好** |
| `DebugTrace` | ✅（`DebugFlags.detail` 控制 detail） | ⚠️ 但 `fail`/`event` 不受 verbose 控制 | 同步写，热路径风险 |
| `GuestBreadcrumb` | ❌ 无法整体关（199 处调用无开关） | ❌ | 只能靠 `attachFile` 前缓冲 |
| `StateProbe` / `DexMemDump` / `WebViewWatch` | ⚠️ 各自内部判断 | ❌ 无测试 | 各自写文件，无统一出口 |
| `diag/DiagnosticsSink` | — | — | 死代码 |
| `FsOpStats` | ✅（限流） | ⚠️ 需真机 | 结构同构于 `g_java_io_*` |

---

## 四、优化计划（分三期，**均未执行**）

### P1 —— 消除"同名接口"与"死代码"（低风险，先做）

| # | 动作 | 判据 |
| --- | --- | --- |
| 1.1 | **删除** `virtualization/diag/DiagnosticsSink.kt`（零引用） | `grep` 无引用；编译通过 |
| 1.2 | 若保留其"文件+快照"语义，则**并入** `diagnostics/`：新增 `Diagnostics.snapshot(name, value)`，不新建第二套接口 | `Diagnostics` 接口方法数 == 4 |
| 1.3 | 把决策写进 `docs/设计/设计-诊断输出抽象层.md`（现状章节） | 文档与代码一致 |

**风险**：极低（死代码）。**预计**：-78 行。

### P2 —— 统一"落盘出口"（中风险，收益最大）

| # | 动作 | 判据 |
| --- | --- | --- |
| 2.1 | 定义 `LogSink`（异步，与 `FileSink` 同一个 worker 模型），替换 `VirtualApplicationManager` 里 **12 处裸写** | 该文件 `FileOutputStream` 计数 = 0 |
| 2.2 | `DebugTrace.FileLogTree` 改为**异步**（复用 2.1 的 worker） | 热路径（`handleMessage`）无同步 I/O |
| 2.3 | 产出一份"日志文件清单"（文件名 / 写入方 / 何时轮转 / 怎么取） | 文档存在且与实际一致 |

**风险**：中 —— `DebugTrace` 是排查主力，改异步后**进程被杀可能丢尾**。
**缓解**：`fail`/`error` 级别保持**同步**（量小），`detail` 走异步。

### P3 —— 合并 native 统计设施（低风险）

| # | 动作 | 判据 |
| --- | --- | --- |
| 3.1 | 抽 `struct OpStats { total, translated, fail }` + `recent[32]` + `note()` 为一个通用设施 | native 里只有**一份**环形缓冲实现 |
| 3.2 | `g_java_io_*` 与 `g_fs_*` 都改用它，JNI 侧合成一个 `nativeGetDiagStats()`（输出按操作分段） | 现有 `nativeGetStats` / `nativeGetFsStats` 保留为薄封装（不破坏调用方） |

**风险**：低（内部重构，无行为变化）。**预计**：-60 行。

### 不建议做的

| 项 | 为什么不做 |
| --- | --- |
| 给 `GuestBreadcrumb` 的 199 处调用加开关 | 改动面太大、且它的价值恰恰在"无条件记录"（怕漏掉静默失败） |
| 把 `StateProbe`(553) / `DexMemDump`(237) 拆小 | 它们是**独立诊断工具**，不属于"重构要拆的混杂文件"；按项目判据（职责是否单一）它们各自职责是单一的 |
| 统一 `DebugTrace` 与 `GuestBreadcrumb` | 两者输出目的不同（事件流 vs 有序生命周期），合并会让两边都变差 |

---

## 四·五、更正（2026-11-25，经用户要求复核后）

> 本文关于 irtualization/diag/DiagnosticsSink 的两处结论**已更正**，
> 详见 `docs/输出/调研/调研-上游诊断体系可用性验证.md`。

| 本文原结论 | 更正后 |
| --- | --- |
| 「两个同名 `DiagnosticsSink` 接口…一个是死代码（最严重）」 | **不是死代码，是「未接线」**。两者是**不同层**：`diag/` 负责把 199 处既有埋点导出成程序流（`onEvent`/`onSnapshot`），`common/diagnostics/` 负责新增可断言的验证点（`onCheck`/`onError`/`onStage`）。**互补，不是重复** |
| P1「**删除** `diag/DiagnosticsSink.kt`（零风险，-78 行）」 | **撤销**。改为：先给 `core:virtualization` 加测试配置并验证它真能装/能收/能还原（A 步），再评估是否接线 |
| 「重构时没先查既有 `diag/`」 | 这条**仍然成立**，但结论应是「应该复用它的桥接能力」，不是「它冗余」 |

**复核依据**：
- 实现完整（`@Volatile` + `inline` 早退 + `install`/`reset`/`isInstalled` 齐全），静态可用
- 但三处接线全缺：`:core:diagnostics` 模块不存在、`app/src` 无 `debug` 源集、桥接器不存在
- 外部引用 0 处 → 运行时 `sink` 恒为 `NoOp`

## 五、一句话结论

> **独立性总体尚可**（`DebugFlags` 是唯一开关源、`diagnostics/` 可测可关），
> **但重复度有三处实质问题**：
> 1. **两个同名 `DiagnosticsSink`**（一个是死代码）—— 重构时没先查既有设计
> 2. **40+ 处裸写文件**，同步与异步两套并存
> 3. **native 两套同构统计**（`g_java_io_*` 与 `g_fs_*`）
>
> 建议顺序：**P1（删死代码，零风险）→ P3（合并 native 统计，低风险）→ P2（统一落盘，收益最大但需谨慎）**。

---

## 六、执行前置条件（用户批准后再做）

1. 每一期单独提交、单独 CI（按项目约定：一次一个构建）
2. P2 前必须先有"日志不丢"的验证手段（否则改异步无法证明不劣化）
3. 三期都不触碰业务逻辑，只动诊断设施 —— 若发现需要动业务，拆出去单独走变更流程
