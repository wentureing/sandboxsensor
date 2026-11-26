# DiagFiles（core/common/diagnostics/DiagFiles.kt）

## 职责

**负责**：诊断文件名与目录的**唯一 Kotlin 侧规范**——所有 `.log` / `.dex` / `.trace` 名字都从这里生成；提供目录扫描与总量统计（探针 D 系列按同规则匹配）。

**不负责**：落盘（`DiagOutput` / `DiagChannel`）、内容格式、轮转策略；**也不负责 C++ 侧的同名白名单**（见「约束与坑」#1）。

## 函数

| 签名 | 作用 |
| --- | --- |
| `breadcrumb/stackDump/hijackTrace/startActivityTrace(pid)` | 带 pid 的固定通道 |
| `webViewWarm/webViewWatch(pid)` | WebView 诊断通道 |
| `stateProbe/dexMem(pid)`、`shortcutProbe(pid)` | 历史 vXX 探针通道（L1 补齐） |
| `memDexDump(pid, index)` | 内存 dex 二进制（`.dex`） |
| `v139Probe/v144IntentProbe/v145IntentProbe/v148ConnectProbe()` | 固定名 probe 通道（L1 补齐） |
| `filtered(prefix)` | `v155-init-filtered.log` 这类过滤结果 |
| `trace(prefix, pid)` | API trace 原始文件（`.trace`，非 `.log`） |
| `list(filesDir)` / `totalBytes(filesDir)` | 扫描 `.log`/`.dex` 并求和（供"是否爆盘"判断） |

## 偏差登记

| 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- |
| `list()` 只认 `.log` / `.dex`，不认 `.trace` | `.trace` 是 API trace 原始文件（MB 级），不参与"诊断文件总量"统计 | 中性偏差 | `.trace` 存在但 `totalBytes` 不计 |
| `filtered(prefix)` 把前缀交给调用方 | `v155-init` / `v156-load` / `v155-click` 由各探针决定 | 中性偏差 | 调用点传 `prefix` |
| 文件名保持 `v1xx-` 命名 | 探针/排查脚本按名匹配；改名等于让历史证据失效 | 必要偏差 | 与 `docs/输出/调研/调研-诊断体系收敛与框架选型.md` §五-1 一致 |

## 约束与坑

1. **C++ 侧有一份平行白名单，已经落后**：`native-hook.cpp` 的 `is_container_diag_path()`（约 `:7138`）只列了 6 项
   （`debug-trace.log`、`checks.log`、`breadcrumb-`、`stackdump-`、`webviewwarm-`、`webviewwatch-`），
   本文件已有 20+ 项。**新增通道必须同时补 C++ 白名单**，否则该文件的写入会占用 Java IO 日志配额（200 条）并在 logcat 里刷屏。
   已登记为待收敛项（见 `docs/输出/变更/20261126-l1-诊断输出统一与环形.md` 审计节）。
2. **新增通道的步骤**：① 本文件加函数；② 调用方只传名字给 `DiagOutput`；③ 补 C++ `is_container_diag_path`；④ 补 `DiagChannelTest` 的 `C4` 断言。
3. 名字里**不要带冒号/空格**：探针按 `-<pid>.log` 后缀匹配。
