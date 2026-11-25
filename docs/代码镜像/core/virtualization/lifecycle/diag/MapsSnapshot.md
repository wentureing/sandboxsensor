# MapsSnapshot（core:virtualization / lifecycle/diag）

- 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/diag/MapsSnapshot.kt`
- 测试：`core/virtualization/src/test/java/com/nextvm/core/virtualization/lifecycle/diag/MapsSnapshotTest.kt`（G1–G9）
- 来源：从 `VirtualApplicationManager` 抽出（v59 REAL maps 快照，B4-2）
- 日期：2026-11-25

## 职责

**负责**：把**内核视角**的 `/proc/self/maps` 文本解析成统计（`RealSnap`），并生成日志行、写出原始文本。

**不负责**：

| 不负责 | 在哪 |
| --- | --- |
| 真正读取内核 maps（raw syscall） | `NativeHookBridge.readRealMaps()`（C++ `read_real_maps`），入口 `VirtualApplicationManager.realSnap()` |
| 何时拍快照（before/after/final 的时机） | `VirtualApplicationManager` 的 v59 流程 |
| 写 breadcrumb 通道 / 决定日志级别 | `GuestBreadcrumb`（本类只产出字符串） |
| 目录选择（`v59DataDir/files`） | 调用方传入 `filesDir` |

**一句话**：本类回答"这段 maps 文本说明了什么、该写成哪一行、原文放哪"，不回答"什么时候读、写哪去"。

## 函数

| 函数 | 签名 | 作用 / 关键前提 |
| --- | --- | --- |
| `parse` | `(text: String): RealSnap` | **纯函数**。逐行解析：可执行段进 `execRanges`；**无 path 的可执行段**累加 `anonExecBytes`；`0x80000..0x200000` 的匿名可执行段进 `bigAnon`（格式 `0x<size>/<perms>@<range>`）；含 `DexHelper` / `memfd` 的行单独收集。畸形行跳过，不抛 |
| `formatLine` | `(tag, snap, prev): String` | **纯函数**。`snap=null` → `v59 realmaps <tag> UNAVAILABLE`；`prev=null` → 全部算新增；否则 `newExecRanges = (snap - prev).size`、`newBigAnon` 只保留 `@` 之后地址区间不同的项 |
| `dump` | `(filesDir: File, pid: Int, tag: String, snap: RealSnap): String` | 写 `realmaps-<pid>-<tag>.txt`（内容是 maps 原文），返回文件名。**IO 失败抛出**，由调用方 `catch` 并记 `logThrowable` |

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | `bigAnon` 的上限写死 `0x200000`（2 MB） | 经验值：SecNeo 解密后的 dex 映射通常 ≥512 KB；2 MB 以上多为 JIT/GC 噪声 | 中性偏差 | breadcrumb `bigAnon=...` 列表 |
| 2 | 只统计**无 path** 的可执行段为"匿名" | file-backed 段（框架/系统库）不是"解密后payload"，计入会掩盖真实信号 | 必要偏差 | `anonExecBytes` 数值 |
| 3 | `newBigAnon` 只比较 `@` 之后的地址区间，不比较大小 | 同一区间的权限/大小变化不算"新映射"；只有新地址才是新映射 | 中性偏差 | 两次快照的 `newBigAnon` |
| 4 | 畸形 range（`zzzz-yyyy`）可能进 `execRanges`，但不累加字节 | `exec.add(range)` 在十六进制解析之前；字节统计有 `toLongOrNull` 兜底 | 中性偏差（已由 G4 铆钉） | `execRanges` 数量 |

## 约束与坑

1. **`parse` 不做 I/O**，因此可以纯 JVM 测（G1–G9）；真正的内核读取在 `realSnap()`，只能在真机验证。
2. **`dump` 的目录拼接由调用方负责**：原实现是 `File(v59DataDir, "files")`，本类只接收最终目录 —— 搬家时特意保留这个边界，避免把"沙箱目录结构"知识复制进来。
3. **日志字符串必须逐字保持**：`v59 realmaps <tag> lines=... anonExecBytes=0x%x newExecRanges=... bigAnon=... newBigAnon=... dexHelper=... memfd=...`；现有排查靠 `grep "v59 realmaps"`。
4. **不要在 `parse` 里加"路径翻译"**：这是**内核视角**的原始 maps，翻译会破坏 v59 的用途（证明 payload 是否真的映射）。
5. `formatLine` 的 `UNAVAILABLE` 分支保留在原语义里（`snap=null`），不要改成抛异常 —— 老日志依赖它判断"读不到"。
