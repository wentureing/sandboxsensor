# InitTrace（core:virtualization / lifecycle/diag）

- 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/diag/trace/InitTrace.kt`
- 测试：`core/virtualization/src/test/java/com/nextvm/core/virtualization/diag/trace/InitTraceTest.kt`（T1–T9）
- 来源：从 `VirtualApplicationManager` 顶层抽出（v155/v156 方法追踪，B4-3c）
- 日期：2026-11-25

## 职责

**负责**：`Debug.startMethodTracing` 的**启停协调**与**二进制 trace 的过滤**：
把 ART SLOW v3 的 trace 过滤成只含关注方法（风控/设备信息/包信息/桥接失败链）的可读日志。

**不负责**：

| 不负责 | 在哪 |
| --- | --- |
| 何时开始 trace（CM 包 bind 入口、`LoginActivity` resume） | `VirtualApplicationManager` 调用 `startInit()` / `startLoad()` |
| 点击类 trace（v127/v134）本身 | `VirtualApplicationManager` 的点击包装器；只在开始前调 `stopBeforeStart` |
| 日志通道 | `GuestBreadcrumb`（本类只调用它） |
| trace 文件拉取/上报 | 外部脚本 |

**一句话**：本类回答"trace 怎么启停不互相踩、二进制记录怎么变成可读行"。

## 函数

| 函数 | 签名 | 作用 / 关键前提 |
| --- | --- | --- |
| `stopBeforeStart` | `(dir: File, tag: String)` | 先 `stopMethodTracing()`：同设备二次 start 是**静默 no-op**（v152 实测 0 字节）。成功后置 `stoppedByClick`，并异步过滤 init/load 两个 trace |
| `startInit` | `()` | 64 MB / 20 s 的 init trace；20 s 后若已被点击停掉则跳过 |
| `startLoad` | `()` | 256 MB / 20 s 的 load trace；**先** `stopBeforeStart`，再置 `loadStarted`（顺序不能反） |
| `filter` | `(traceFile, dir, outName, tag, sampled)` | 读 trace → `textEnd` → `parseMethods` → 校验 `SLOW` 与 `recSize` → `collectRecords` → 写 `outName` |
| `textEnd` | `(ByteArray): Int` | **纯函数**：`*end\n` 的 `*` 下标，找不到 -1 |
| `parseMethods` | `(String): Map<Int,String>` | **纯函数**：`*methods` 段 `hexId\tclass\tmethod` → `id -> "class.method"` |
| `isSlow` / `u32` | `(ByteArray, Int)` | **纯函数**：块头校验 / 小端 u32 |
| `matchesAny` | `(name, keys)` | **纯函数**：方法名含任一关注前缀 |
| `collectRecords` | `(data, startOff, recSize, methods, keys, sampled)` | **纯函数**：按固定记录大小遍历，产出 `t2=.. tid=.. name`；`sampled=false` 跳过 `mid & 1` 的退出记录；**`recSize <= 0` 立即返回**（否则 `off += 0` 死循环） |

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | 关注方法用**硬编码前缀表**（`KEYS`，15 条） | 定向排查 CM 风控/设备信息链路；上游没有等价能力 | 必要偏差 | `v155-filter done matched=N` |
| 2 | 过滤只保留 `ENTER` 记录（`sampled=false` 时） | 退出记录无时间信息价值，且会让行数翻倍 | 中性偏差 | 行数对比 `total` |
| 3 | 固定 20 s 窗口 / 64 MB（init）、256 MB（load） | v153 的 1 ms 采样器把 guest 冻死（实测 5 次 ANR）→ 回到全量 trace | 必要偏差 | `v155-trace start file=...` |
| 4 | 唯一记录写入名 `v155-init-filtered.log` / `v156-load-filtered.log` / `v155-click-filtered.log` | 现有排查脚本按名抓取 | 必要偏差 | 文件名 |

## 约束与坑

1. **必须先 stop 再 start**：同设备二次 `startMethodTracing` 是静默 no-op（v152 实测 click trace 0 字节）。
2. **`startLoad` 里 `loadStarted` 必须置在 `stopBeforeStart` 之后**：否则 `stopBeforeStart` 会把 load trace 自己标成 `stoppedByClick`，20 s 定时器直接跳过停止。
3. **`recSize < 14` 或找不到 `SLOW`/`*end` 必须直接返回**：继续解析会越界读。
4. **`mid & -4`** 取方法 id（低 2 位是标志位），不要改成 `mid` 本身。
5. **过滤跑在后台线程**（`v155-trace-filter` 等），不能在主线程做（读 64 MB）。
6. 本对象持有跨调用的 `@Volatile` 状态（4 个布尔），**每个进程只有一份**；多实例不适用（与容器多实例暂停一致）。
7. **trace 字节布局**（写测试/解析时最容易错的地方）：
   ```
   文本头（"ART SLOW v3" + *methods + ...）→ "*end\n" → 魔数 "SLOW"(4 字节)
     → 块头 28 字节（recSize 在块头偏移 12，即 blobOff+16）→ 14 字节记录 × N
   ```
   记录起点 = `blobOff + 32`（4 + 28）。把块头当 32 字节或把 recSize 写到偏移 16，
   会让记录起点错位、甚至 recSize 读成 0 → `collectRecords` 死循环（2026-11-25 实测 CI 卡 8 分钟）。
