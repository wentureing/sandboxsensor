# GuestMemoryDump（core:virtualization / lifecycle/diag）

- 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/diag/GuestMemoryDump.kt`
- 测试：`core/virtualization/src/test/java/com/nextvm/core/virtualization/lifecycle/diag/GuestMemoryDumpTest.kt`（G1–G5）
- 来源：从 `VirtualApplicationManager` 机械抽出（`docs/输出/变更/20261125-B4-VirtualApplicationManager拆包.md`）
- 日期：2026-11-25

## 职责

**负责**：guest 进程内的两种 **offline 转储**（线程栈 / 内存 dex）+ 小端读取工具。

**不负责**（边界）：

| 不负责 | 在哪 |
| --- | --- |
| 何时转储（watchdog / +N 秒调度） | `VirtualApplicationManager.startWatchdog` 等调用方 |
| 写文件通道/轮转/超限 | `DebugTrace` / `core:common/diagnostics.DiagChannel`（本类只 `appendText` 到给定 `File`） |
| 启动链控制流 | `VirtualApplicationManager`（bind/createApplication/Providers） |
| `/proc/self/maps` 伪造与路径翻译 | C++ `native-hook.cpp` |
| `dumpRealMaps`（maps 快照对比） | 仍在 `VirtualApplicationManager`（依赖 `RealSnap`），下一批搬 |

**一句话**：把"转储怎么做"集中到一处，让启动链文件只剩编排。

## 函数

| 函数 | 签名 | 作用 / 关键前提 |
| --- | --- | --- |
| `dumpGuestStacks` | `(out: File, label: String)` | ① 先 dump **主 Looper 待处理消息**（反射 `MessageQueue.mMessages`，最多 20 条，含 `when/what/callback/target`）；② 再 dump **全部线程栈**（`main` 优先，其余按名字排序，每线程最多 30 帧）。**必须**经 `DiagSuspendGate.lock` 串行化（见「约束与坑」1）。结果 `appendText` 到 `out`；IO 失败静默 |
| `dumpMemoryDex` | `(application: Application)` | 扫 `/proc/self/maps` 中**匿名可读**与 **`/memfd:`** 映射（跳过 `[..]`、file-backed、`jit-cache`），按 1 MB chunk 读 `/proc/self/mem`，找 dex magic，校验 header 后把整段写到 `<filesDir>/memdex-<pid>-<n>.dex`。**必须在后台线程调用**（见「约束与坑」2） |
| `le32` | `(b: ByteArray, off: Int): Int` | 小端 32 位读取；**不做边界检查**（调用方保证 `off + 4 <= size`） |

**`dumpMemoryDex` 的校验条件**（缺一不写）：`magic == dex\n035/037/038/039` + `header_size == 0x70` + `endian == 0x12345678` + `0x70 <= fileSize <= 256 MB` + `off + fileSize <= 映射尾` + `0 < mapOff < fileSize` + `fileSize:checksum` 未写过。

**上限**：每进程最多 **64 个 blob**、总计 **512 MB**；已 dump 的地址区间登记在 `dumpedRanges`，避免同一 dex 被重复扫到。

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | 反射读 `MessageQueue.mMessages` 与 `Message` 私有字段（`when`/`callback`/`target`/`what`/`next`） | 公开 API 拿不到"待处理消息"；只有私有字段能回答"splash 卡住时队列里有什么" | **必要偏差**（隐藏 API） | 失败时输出降级为 `main looper queue: <Exception>` |
| 2 | 主 Looper 只 dump 20 条、每线程只 30 帧 | 体积与耗时；足够判断"卡在哪个回调" | 中性偏差 | 输出里的 `when=+..s` 行数 |
| 3 | 只认 4 个 dex magic 变体（035/037/038/039） | 覆盖 Android 各版本产物；新版本若加 magic 需补 | 中性偏差 | `memdex-*` 文件是否出现 |
| 4 | 只扫匿名 + `/memfd:` 映射，跳过 file-backed | host/framework 的 dex 是噪音；shell 解密后的 dex 在匿名/memfd | 必要偏差 | `v150-memdex scan started ... memfd=on` |
| 5 | 完全不解析 VMP 保护的方法体 | 加固壳的方法体是 VM 字节码，不是 dex | 中性偏差（**扫不到本身即结论**） | breadcrumb `memdex dumped` 数量 |
| 6 | `appendText` 失败静默、`FileOutputStream` 失败删半成品 | 诊断失败不能影响主流程 | 必要偏差 | `memdex scan aborted` 日志 |

## 约束与坑

1. **任何会 suspend 线程的诊断都必须走 `DiagSuspendGate.lock`**。
   `Thread.getAllStackTraces()` 会 suspend 全部线程；历史上它（+8 s 的 stackdump）与
   `StateProbe` 的线程普查（+7 s）**并发 suspend**，触发 ART 的
   `SuspendThreadByPeer timeout` → **guest 直接 abort**。`StateProbe` 已改为不 suspend，
   本类用 gate 保证"未来再加的 suspend 诊断不会重叠"。
2. **`dumpMemoryDex` 只能在后台线程跑**：它读最多 512 MB、1 MB 一次；
   在 main 上会卡住启动。v121 探针注明为 read-only background probe。
3. **读 `/proc/self/mem` 依赖同进程自身权限**；被 SELinux/加固壳挡住时静默 abort（只记 breadcrumb）。
4. **文件名由调用方/本类分工决定**：`stackdump-<pid>.log` 由调用方传入 `out`；`memdex-<pid>-<n>.dex` 由本类生成。
   两者都写进 guest 的 `filesDir`，与探针 D 系列（`breadcrumb`/`checks`/`stackdump`）的命名规则一致。
5. **不要把"计时/调度"搬进来**：调用时机（watchdog、+N 秒）留在 `VirtualApplicationManager`，
   否则诊断会重新和控制流混在一起——这正是本次拆分要解决的问题。
6. `le32` 不做边界检查是**故意的**（dex header 校验路径上每字节都已确认可读）；
   若要复用到别处，必须先加长度判断。
