# SlotStatus

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/process/SlotStatus.kt`
> 来源：2026-11-27 process slot 会话复用（Step 0a，纯新增）

## 职责

**只回答一个问题：一个 process slot 现在处于什么状态，UI 需要哪些字段把它显示出来。**

- 负责：状态枚举（`SlotState`）+ 一行状态数据（`SlotStatus`）+ 探测输入的纯数据形态
  （`ProcSnapshot` / `TaskSnapshot` / `RecordSnapshot`）+ 判定输出（`ReconcilePlan`）。
- **不负责**：怎么探测（`SlotStatusProbe`）、怎么判定（`SlotReconciler`）、怎么执行清理（`VirtualEngine`）。

## 函数

| 类型 | 内容 | 关键约束 |
| --- | --- | --- |
| `enum class SlotState` | `FREE / RUNNING / STALE / ORPHAN / CONFLICT` | `FREE` 不出现在 `statuses` 里（只表示"这个槽没用"） |
| `data class SlotStatus` | `slot / state / instanceId? / packageName? / pid / taskId / topComponent? / note` | 全部是 String/Int，**不含任何 Android 对象**，可直接在 Compose 里渲染 |
| `data class ProcSnapshot` | `slot / pid` | 由 `SlotStatusProbe` 从系统进程名解析 |
| `data class TaskSnapshot` | `taskId / slot? / instanceId? / numActivities / topComponent?` | `instanceId` 来自 `baseIntent` 的 `_nextvm_instance_id`，可能为空 |
| `data class RecordSnapshot` | `instanceId / packageName?` | 只用于补齐 UI 上的包名显示 |
| `data class ReconcilePlan` | `statuses / releaseLeases / releaseStubsForSlots / markNotRunning / orphans` | `statuses` 已按 slot 升序；`release*` 只含 STALE |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 新增 `note` 字段 | AOSP 没有对应概念；用来在 UI 上解释 `ORPHAN` / `CONFLICT` 的来源（见变更文档 §3.4） | 必要偏差（可诊断性） | `SlotReconcilerTest` S2/S3/S4/S5/S8 断言 note 内容 |
| 状态枚举 5 个 | AOSP 只有 process state（RUNNING/CACHED…）；这里把"租约 vs 系统"的错配显式化 | 必要偏差（容器自造概念） | 同上 |

## 约束与坑

- `SlotStatus.state == RUNNING` **不等于**"界面在前台"：它只表示"租约在 + 进程在"。是否可见要看 `topComponent`/`taskId`（后续步骤可能加 `isVisible`）。
- `packageName` 可能为 `null`（`runningApps` 里没有该实例时）；UI 必须能显示空值，不要 `!!`。
- `taskId == 0` 表示"该会话没有可用的系统 task"（进程在但 Activity 全 finish，见变更文档 §3.4.2）——这不是错误态。
- 这个文件**不许**引入 Android import（否则纯 JVM 单测跑不了）。
