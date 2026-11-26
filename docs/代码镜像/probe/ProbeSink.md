# ProbeSink（probe/ProbeSink.java）

## 职责

**负责**：探针项的**收集出口**——套件过滤（`ProbeSuite.enabled`）、`skipped` 记账、JSON 条目累积。

**不负责**：具体探针项的实现（`PathProbes` / `ProcProbes` / `FsProbes` / `NativeProbes` / `DiagProbes`）、报告组装（`ProbeRunner.run`）、native 结果解析（`ProbeRunner.probeNative` 自己解析后直接写 `sink.entries`）。

## 函数

| 签名 | 作用 | 前提 |
| --- | --- | --- |
| `ProbeSink(ProbeSuite)` | 构造，绑定本轮套件 | 每轮一个实例 |
| `put(id, group, name, probe)` | 注册一项；套件不覆盖时自动转 `skipped` | `probe` 抛异常即记 `fail`（由 `Report.put` 处理） |
| `putSkipped(id, group, name)` | 显式记 `skipped` | 用于"未采样/超时"这类非失败情形 |
| `entries` | 报告里的 entries 数组 | 直接写入方须自行 `synchronized` |

## 偏差登记

| 偏差 | 为什么 | 类型 | 观测 |
| --- | --- | --- | --- |
| 没有"全局项数"断言，只保证注册路径一致 | 项集合契约由 `tools/lint/check-probe-ids.mjs` + `expected_probe_ids.txt` 在源码层锁定；运行时项数会被 suite 与采样情况影响 | 必要偏差 | golden 99 项 vs 报告 98 项（差值 = 兜底项 `N00`） |
| native 结果绕过 `put` | N 系列 id 来自 C++ 输出，需先解析再判断 | 必要偏差 | `ProbeRunner.probeNative` 里自己 `suite.enabled` |

## 约束与坑

1. **`synchronized (sink.entries)`**：直接写 `entries` 的地方（`probeNative`、`probeUiEntries`）必须加锁；`put/putSkipped` 内部已加锁。
2. **`skipped` ≠ `fail`**：套件过滤、未采样都记 `skipped`；只有"该产出却没产出"才记 `fail`。
3. **新增/删除探针项后必须**：`node tools/lint/check-probe-ids.mjs --write` 更新 golden，否则 CI 的项集合检查会红。
