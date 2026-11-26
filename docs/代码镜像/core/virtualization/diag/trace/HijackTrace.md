# HijackTrace

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/diag/trace/HijackTrace.kt`
> 来源：B4-4d 从 `VirtualApplicationManager.scheduleHijackTrace` 原样搬出（2026-11-26）

## 职责

**只回答一个问题：什么时候去采样壳（hijack/SecNeo）SDK 的初始化终态。**

- 负责：仅对 `com.sinovatech.unicom.ui` 起一个 daemon 线程，在 bind 之后 `+1/+4/+12/+25s`
  调 `HijackProbes.sample` 与 `HijackProbes.unwrap`，写 `hijacktrace-<pid>.log`；每进程一次。
- **不负责**：采样什么（`HijackProbes`）、stock-init 换装（VAM 的 `swapInStockInstrumentation`）、
  线程栈转储（`GuestStackDumps`）。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `isTarget(packageName: String): Boolean` | 只对联通 guest 生效（**精确**比较） | 纯函数 |
| `schedule(packageName, stockInitNote, stockInitExperiment, unwrapEnabled)` | 起线程按 4 个时间点采样 | 每进程一次；失败全吞；`stockInit*` / `unwrapEnabled` 由 VAM 的实验状态传入（v34/v35 实验，默认关） |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 目标包抽 `TARGET_PACKAGE` + `isTarget()` | 原为内联 `if (packageName != "com.sinovatech.unicom.ui") return` | **必要偏差**（可测性），语义等价（仍是精确比较） | `HijackTraceTest` H1–H4（含"前缀相似不算"） |
| 3 个实验状态改成**参数**传入 | 原来直接读 VAM 字段 `stockInitNote` / `stockInitExperiment` / `UNWRAP_ENABLED` | **必要偏差**（跨文件依赖），语义等价：VAM 调用处传当前值，取值时点同为调用瞬间 | 等价检查忽略这些标识符后 **479 字符逐字相同** |

## 约束与坑

- **上游遗留的实验开关（规则 18.2 指出，本批不改）**：`STOCK_INIT_ENABLED = false`、`UNWRAP_ENABLED = false`
  是 v34（摘除 hijack proxy）/ v35（换入 stock Instrumentation）两个实验的死开关，注释写明"flip the const to re-run"。
  它们仍在 VAM 里，本批按原样传参，**没有删除也没有启用**。
- 只对联通 guest 追踪：其它 guest 没有那条 `HijackSDK.initailise` 初始化链，采样只会得到 `none`。
- `HijackProbes.unwrap(...)` 带 `unwrapEnabled` 参数：即使 `schedule` 被调用，摘除动作默认也不执行 —— 别以为"采到 hijacktrace 日志就代表做了摘除"。
- 时间点 `+1/+4/+12/+25s` 是"init 完成度"的采样面；缩短会让"proxy 是否停在 hijack 状态"看不出来。
