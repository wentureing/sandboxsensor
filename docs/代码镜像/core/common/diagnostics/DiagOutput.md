# DiagOutput（core/common/diagnostics/DiagOutput.kt）

## 职责

**负责**：诊断文件的**唯一落盘出口**。调用方给「通道名 + 内容」，本对象负责目录绑定、通道缓存、上限与失败静默。

**不负责**：文件名规范（`DiagFiles`）、格式化（调用方）、级别门控（`DebugFlags`）、限流（调用方，见既有调研 §五-2）。

## 函数

| 签名 | 作用 | 前提 / 副作用 |
| --- | --- | --- |
| `setDir(File?)` | 绑定当前诊断目录 | **清空通道缓存**；`null` = 未就绪（等价 release） |
| `dir(): File?` / `isReady(): Boolean` | 读当前目录 | — |
| `append(name, text, maxBytes): Boolean` | 追加一段文本 | 未绑定目录返回 `false`；内部 `mkdirs` + `DiagChannel` |
| `line(name, text, maxBytes): Boolean` | 追加一行（补 `\n`） | 同上 |
| `overwrite(name, text): Boolean` | 整体覆盖（一次性快照：maps 等） | 绕过通道缓存 |
| `size(name): Long` | 当前大小 | 未绑定返回 0 |
| `reset()` | 清缓存 + 清目录（**仅测试**） | — |

## 偏差登记

| 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- |
| 与 `DebugTrace` 分家（`DebugTrace` 仍自己持有 `File`，不走本对象） | `debug-trace.log` 的目录在**进程启动时**就确定，而本对象的目录会随 guest bind 改；合流会让主进程日志串到 guest 目录 | 必要偏差 | `debug-trace.log` 始终出现在进程 `filesDir` 根，不在 `virtual/data/<inst>/files` |
| 目录是**进程级全局**，不是按实例 | 多实例已暂停（STATUS），当前一进程一 guest | 待修偏差（多实例恢复前必须改） | 两个 guest 先后 attach → 前者日志落到后者目录（见「约束与坑」#2） |

## 约束与坑

1. **必须在 `GuestBreadcrumb.attachFile` 之前完成 `setDir`**，否则早期事件走内存缓冲（`MAX_BUFFER_LINES = 4000`），超过即丢。
2. **全局目录串号**：同一进程内再 bind 另一个 guest 会覆盖 `dir`，前一个 guest 的后续日志写进新目录。多实例恢复前不要依赖本对象做跨实例隔离。
3. **`append` 在调用方锁内可能被调**（`GuestBreadcrumb.log` 持自己的锁再调本对象）→ 锁序固定为「调用方锁 → `DiagChannel` 锁」，**禁止反向**，否则死锁。
4. **未绑定目录 = 静默丢弃**，不抛异常、不建文件 —— 这是 release 安全的实现基础，别改成"回退到 `context.filesDir`"。
5. 通道缓存键是 `dir.path + "/" + name`，`setDir` 必须 `clear()`，否则切目录后仍写旧文件（已实现，勿删）。
