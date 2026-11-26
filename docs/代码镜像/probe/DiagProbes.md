# DiagProbes（probe/DiagProbes.java）

## 职责

**负责**：D01–D05 五项诊断通道断言——breadcrumb 存在 / `checks.log` 行数 / stackdump 三态 / 诊断文件总量 / `checks.log` 尾部。

**不负责**：写诊断文件（那是容器的 `DebugTrace`/`DiagChannel`）、判断"诊断是否正常"（探针只报事实，判定在人与脚本）。

## 函数

| 签名 | 作用 | 前提 |
| --- | --- | --- |
| `run(ctx, sink, startedElapsedMs)` | 注册 D01–D05 | `startedElapsedMs` 用于 D03 区分 `not-yet` / `missing` |

## 偏差登记

| 偏差 | 为什么 | 类型 | 观测 |
| --- | --- | --- | --- |
| D03 分三态（`exists` / `not-yet` / `missing`） | watchdog 在 bind 后 `+0.3s…+20s` 才转储；报告几乎总早于它，原来 `exists=false` 会被读成"能力缺失" | 必要偏差（2026-11-26） | value 里的 `state=` 与 `ageMs=` |
| D03 阈值 `25s` 硬编码 | 与 `VirtualApplicationManager.scheduleGuestStackDumps` 的最后一批（+20s）留余量 | 中性偏差 | `STACKDUMP_WAIT_MS` |
| D02/D05 只读前 `64KB` | `/proc` 与日志可能很大，探针不该把内存拉满 | 必要偏差 | `readText(f, 64*1024)` |

## 约束与坑

1. **不要在这里读太新的文件**：D02/D05 读 `checks.log`，若容器刚启动，行数会偏少；这是"时序事实"，不是缺陷。
2. **D04 只统计 `.log` / `.dex`**：`.trace` 与 `.tsv` 不计入，与 `DiagFiles.list()` 的规则一致。
3. **D03 的 `not-yet` 是正常态**：脚本判定时应把 `state=not-yet` 当"未到时"，只有 `state=missing`（>25s）才当异常。
