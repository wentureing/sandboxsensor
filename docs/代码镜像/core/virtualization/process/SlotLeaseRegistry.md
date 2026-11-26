# SlotLeaseRegistry

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/process/SlotLeaseRegistry.kt`
> 来源：2026-11-27 process slot 会话复用（Step 0a，纯新增）

## 职责

**只回答一个问题：哪个实例现在占着哪个 slot / 哪个 stub。**

- 负责：会话租约的登记（`acquire`）、双向查询（`byInstance` / `bySlot` / `all`）、回填系统 `taskId`、释放（`release`）。
- **不负责**：分配 slot（`VirtualProcessManager`）、分配 stub（`StubRegistry`）、判定"是否该归还"（`SlotReconciler`）、
  真正归还 stub（`VirtualEngine`）。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `acquire(slot, instanceId, stubClassName, nowMs): SlotLease` | 登记会话 | **幂等**：同实例重复调用返回既有租约（不覆盖 slot/stub/时间）；同 slot 已被别的实例占用时**不覆盖** `bySlot`（交给 reconcile 判 CONFLICT） |
| `byInstance(instanceId): SlotLease?` | 按实例查 | 无 |
| `bySlot(slot): SlotLease?` | 按槽查 | 同 slot 多实例时返回**最早**登记的那个 |
| `all(): List<SlotLease>` | 全量，按 slot 升序、同 slot 按 `acquiredAtMs` | UI 展示顺序稳定（`SlotReconcilerTest` S10 依赖） |
| `noteTaskId(instanceId, taskId): SlotLease?` | 首次拿到系统 taskId 时回填 | 幂等；实例不存在返回 null |
| `release(instanceId): SlotLease?` | 释放一个会话 | 幂等；返回被释放的租约供调用方决定"要不要归还 stub" |
| `clear()` | 全清 | **不触发任何归还**（容器侧会话全丢时用） |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 容器自造概念 | AOSP 的"进程槽"由 manifest `android:process` 静态决定，没有运行时租约 | 必要偏差（容器需要"实例↔槽"的可观测映射） | `SlotLeaseRegistryTest` L1–L7 |
| 幂等 `acquire` | 原引擎每次都 `resolveStub`（`VirtualEngine.kt:631`），没有"同实例只登记一次"的语义 | 必要偏差（本轮的修复核心） | L1 固定"重复登记不覆盖" |
| 方法级 `@Synchronized` | 租约表可能被 UI 线程（读）与引擎线程（写）同时访问 | 中性偏差 | 无锁竞争证据；不加则 `LinkedHashMap` 可能抛 `ConcurrentModificationException` |

## 约束与坑

- **不覆盖 `bySlot` 是有意的**：`L2`（两实例共用 slot）还没修，覆盖只会让"谁占着槽"变得不可诊断；冲突由 `SlotReconciler` 显式标 `CONFLICT`。
- 租约是**容器主进程内存态**：容器主进程被系统回收后表就空了，而 `:pN` 进程可能还在 → 这就是 `ORPHAN` 的主要来源（变更文档 §3.4.1 O1）。**不要**把租约持久化到磁盘来"修"这个现象：持久化只会让"进程已死"的陈旧租约变成 `STALE` 噪音。
- `release` 之后**不要**假设 stub 已归还——归还由 `VirtualEngine` 在确认"同 slot 无其它租约"后调用 `StubRegistry.releaseAllStubsForSlot`（Step 1）。
- 本类不依赖 Android：新增逻辑必须保持纯 Kotlin，否则 `SlotLeaseRegistryTest` 会失去意义。
