# LaunchPlanner

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/process/LaunchPlanner.kt`
> 来源：2026-11-27 P1（把 `VirtualEngine.launchApp` 的"选路 + 选 stub"决策抽成纯函数）

## 职责

**只回答一个问题：这次打开 guest，具体该发哪个 stub / 该不该切前台 / 该不该动租约。**

- 负责：`LaunchInput` → `LaunchPlan | Failed`；池耗尽错误文案的唯一来源（`noStubMessage`）。
- **不负责**：探测系统状态（`SlotStatusProbe` + `SlotReconciler`）、真正 `startActivity`（`VirtualEngine`）、
  标记 stub 占用（`StubRegistry.markInUse`，由引擎在计划通过后调用）。

## 函数

| 签名 | 作用 | 关键前提 |
| --- | --- | --- |
| `plan(input: LaunchInput): LaunchOutcome` | 三条路径：`BRING_TO_FRONT`（stubClass=null）/ `REUSE_STUB`（租约里的 stub）/ `NEW_SESSION`（候选首个 + 登记租约）；候选为空 → `Failed` | 纯函数；`stubCandidates` 由引擎用 `StubRegistry.candidatesFor()` 取得（**只读**） |
| `noStubMessage(input): String` | `No available stub for process slot N (instance=… mode=…)` | 与引擎日志/`checks.log` **逐字一致**（可 grep） |

**输入字段契约**

| 字段 | 谁提供 | 说明 |
| --- | --- | --- |
| `lease` | `SlotLeaseRegistry.byInstance()` | null = 从没开过 |
| `status` | `slotStatus()` 里按 `slot + instanceId` 过滤 | null = 探测不到（走新会话） |
| `stubCandidates` | `StubRegistry.candidatesFor(slot, launchMode)` | **不标记占用**；只有 plan 通过后引擎才 `markInUse` |
| `sessionReuseEnabled` | `DebugFlags.isOn(SESSION_REUSE, true)` | 置 0 退回旧行为 |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 决策从 `launchApp` 内联块抽出 | 原来在引擎里 `if/else` + `reusedStub` 变量 | **必要偏差**（可测性），语义逐条对应（含"切前台失败降级沿用 stub"） | `LaunchPlannerTest` P1–P9 |
| `stubCandidates` 只读 + 延后 `markInUse` | 原 `resolveStub` 一次完成"取候选 + 标记" | **必要偏差**：决策要能在纯 JVM 里跑，就不能在决策里改池子 | `StubRegistryTest` G3–G5/R1–R3 仍固定原语义；引擎在 `plan.registerLease` 分支才 `markInUse` |
| 错误文案抽 `noStubMessage` | 原来在引擎里拼字符串 | 中性偏差 | P9 逐字断言 |

## 约束与坑

- **`BRING_TO_FRONT` 的 `stubClass` 必须是 null**：一旦非 null，引擎会去 `markInUse` 并启动组件 → 又变回"新 Task"。
  切前台失败时由引擎 `plan.copy(decision=REUSE_STUB, stubClass=租约里的)` 降级（P1 只保证"降级语义是对的"，失败判定在引擎）。
- `stubCandidates` 的顺序就是池子的优先级（`standard00..04`）；**不要排序**，否则 stub 名与 taskAffinity 的对应关系会变。
- `REUSE_STUB` 分支里租约的 stub 为空串时回退到候选首个（P8）——历史脏数据兜底。
- 本文件禁止任何 Android import：`Intent`/`Context` 都在引擎执行层。这样 `LaunchPlannerTest` 不需要 Robolectric。
- 新增"要不要登记租约"这类语义时，只能改 `LaunchPlan.registerLease`，不要在执行层再判断一次（两份判据会漂移）。
