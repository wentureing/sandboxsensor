# SlotReconciler

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/process/SlotReconciler.kt`
> 来源：2026-11-27 process slot 会话复用（Step 0a，纯新增）

## 职责

**只回答一个问题：给定租约、系统进程、系统任务、实例记录，每个 slot 现在是什么状态、该清理谁。**

- 负责：stub 类名 / 进程名 → slot 的解析；五态判定（`RUNNING / STALE / ORPHAN / CONFLICT`，`FREE` 不输出）；产出清理计划。
- **不负责**：取系统数据（`SlotStatusProbe`）、执行清理（`VirtualEngine`）、UI 渲染（`ProcessSlotsScreen`）。

## 函数

| 签名 | 作用 | 关键前提 |
| --- | --- | --- |
| `parseSlotFromStubClass(className: String?): Int?` | `com.nextvm.app.stub.P3$Standard01` → `3`；非 stub → null | 前缀取自 `VirtualConstants.STUB_PREFIX`；`P` 后必须紧跟数字且下一个字符是 `$` |
| `parseSlotFromProcessName(hostPackage: String, processName: String?): Int?` | `com.nextvm.app:p3` → `3`；主进程 / 别的包 → null | 主进程名是 `com.nextvm.app`（无 `:p`），必须返回 null |
| `reconcile(leases, procs, tasks, records, maxSlots): ReconcilePlan` | 五态判定 + 清理计划 | **纯函数**：不读系统、不写状态、幂等（同一输入两次结果相等） |

判定规则（顺序即优先级）：

| 条件 | 状态 | 动作 |
| --- | --- | --- |
| 无租约 + 无进程 | `FREE` | 不输出 |
| 无租约 + 有进程 | `ORPHAN` | 只进 `orphans`，**不清理** |
| 有租约 + 无进程 | `STALE` | 进 `releaseLeases` / `releaseStubsForSlots` / `markNotRunning` |
| 同 slot 多租约 | 最近的判 RUNNING，其余 `CONFLICT` | 只报告 |
| 任务 `instanceId` 与租约不符 | `CONFLICT` | 只报告 |
| 有租约 + 有进程 + 有任务 | `RUNNING`（`taskId` 取任务的） | 无 |
| 有租约 + 有进程 + 无任务 | `RUNNING`，`note="no-task"` | 无（下次用原 stub 重开 Activity） |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| `numActivities <= 0` 的任务被忽略 | 系统可能在最后一个 Activity finish 后留一个空 task（ROM 差异） | 必要偏差（避免把空 task 当成"有界面"） | `S3` / `S13` |
| 同 slot 多租约"最近者为准" | 原实现根本没有租约概念；`L2` 现场需要可诊断而不是静默 | 必要偏差（可观测性） | `S5` |
| `ORPHAN` 不清理 | 用户可能正在用那个进程（变更文档 §3.4.1） | 必要偏差（安全优先） | `S4` 断言"不进任何清理清单" |

## 约束与坑

- **`reconcile` 必须是纯函数**：任何 `System.currentTimeMillis()` / `ActivityManager` 调用都不许进来，否则 S10（幂等）失效，也无法在没有设备的 CI 里跑。
- `markNotRunning` 里给的是 `instanceId`，不是 slot：同一实例多 slot（不该发生）时也不会重复。
- `CONFLICT` 的 `pid` 取该 slot 的进程 pid（可能属于另一个实例）——UI 上必须配合 `note` 解释，不要把 pid 当成"这条记录的进程"。
- `maxSlots` 之外的租约/进程**一律忽略**（`S9`）：`MAX_PROCESS_SLOTS = 10` 与 manifest 的 `P0–P9` 必须一致，改常量要同时改 manifest。
- 解析函数刻意不用正则（避免 Kotlin 字符串里 `$` 的转义歧义），改用 `startsWith` + 数字前缀 + 分隔符判断；改动时保持"非 stub 一律 null"的严格性。
