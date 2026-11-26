# DiagChannel（core/common/diagnostics/DiagChannel.kt）

## 职责

**负责**：单个诊断文件的落盘原语——追加、**超限保留尾部**、单条超限不丢、IO 失败静默并返回 `false`。

**不负责**：目录选择（`DiagOutput`）与文件名规范（`DiagFiles`）；不轮转成多文件、不做压缩与清理。

## 函数

| 签名 | 作用 | 前提 / 副作用 |
| --- | --- | --- |
| `DiagChannel(file, maxBytes=1.5MB, keepTail=true)` | 构造 | 不创建文件，不建父目录（父目录由调用方保证） |
| `append(text): Boolean` | 追加 + 必要时轮转 | 内部 `synchronized`；失败吞异常返回 `false` |
| `size(): Long` | 当前大小 | 不存在返回 0 |

## 偏差登记

| 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- |
| 默认 **`keepTail = true`（环形保留尾部 1/4）**，而非历史「超限清空」 | 清空会把**崩溃前现场**一次性抹掉；实测 `debug-trace.log` 200 KB/1.5 MB，早期 guest 场景很快撞线 | 必要偏差（2026-11-26 L1 起） | 文件里出现 `### rotated @<epoch> ###`，其后仍有内容 |
| `checks.log`（`FileSink`）显式 `keepTail = false` | 它是**结构化检查结果**，被探针 D 系列与 `DiagnosticsSequenceOutputTest` 解析；插入标记行会污染序列 | 必要偏差 | `checks.log` 里永远不出现 `### rotated` |
| 单条 `incoming >= maxBytes` 时不轮转 | 轮转也留不下这条；宁可暂时超限也不丢 | 必要偏差 | 写一条 100 KB 到 `maxBytes=16` 的通道，内容仍在 |

## 约束与坑

1. **`append` 是同步 IO**（`open + write + close`，约 150–1000 µs/次）。**不要在 hook 热路径上逐条调用**：现有 `NativeHookBridge.logPathTranslation` 用 200 条配额挡住，新增调用点必须自带限流。
2. **轮转要读整个文件**（`readText()` 最多 `maxBytes`）。低频（每 1.25 MB 一次），但发生在**调用线程**上；breadcrumb 在 guest 主线程写 → 撞线那次会有毫秒级停顿（已登记为已知代价，不是缺陷）。
3. **文件必须是行边界调用**：轮转按字节切尾，可能切在行中间；调用方按「整行/整段」传入时最坏情况是首行残缺（尾部保留语义决定）。
4. **`maxBytes` 不是硬上限**：rotate 后文件为 `1/4 maxBytes`，加上单条超限的追加，瞬时可能超过 `maxBytes`。
5. **不要给它加"时间轮转/多文件"**：文件名必须保持稳定（`DiagFiles` 是探针与排查脚本的契约）。
