# 代码镜像：`core/virtualization/engine/TransactionDiagnostics.kt`

> 对应代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/engine/TransactionDiagnostics.kt`
> v150 从 `ActivityThreadHook.kt`（原 L330–383）拆出。

## 职责

**做什么**（纯观测，不改控制流）
- `traceDelivery(line)`：把 `ActivityThread` 收到的 activity 事务按行追加到
  `<filesDir>/startactivity-trace-<pid>.log`，用于区分「AMS 根本没投递」与
  「投递了但 guest 侧丢弃」。
- `describeTransactionItem(item, className)`：把 `ClientTransaction` 的 item 压成一行
  （组件名 / 是否带 nextvm extras / 多 Intent 列表）。

**不负责**
- ❌ 不做拦截、不改 classloader、不启动 Activity（`ActivityThreadHook` / `TransactionInterceptor` 负责）
- ❌ 不抛异常 —— 任何失败都降级（见约束 1）
- ❌ 不做统计聚合（只追加写文件）

## 函数

| 签名 | 作用 | 副作用 / 前提 |
| --- | --- | --- |
| `traceDelivery(line: String)` | 追加一条投递见证 | 写 `<filesDir>/startactivity-trace-<pid>.log`；**永不抛**；内部加锁串行化 |
| `describeTransactionItem(item: Any, className: String): String` | item → 单行描述 | 反射读 `mIntent` / `mIntents`；失败返回 `<describe failed>`，**永不抛** |

## 偏差登记

| # | 偏差 | 相对谁 | 类型 | 理由 |
| --- | --- | --- | --- | --- |
| **1** | 依赖隐藏字段名 `mIntent` / `mIntents` | AOSP 内部字段 | ✅ **必要偏差** | 没有公开 API 能读 `ClientTransaction` 的 item 内容；OS 升级改名时降级为 `<describe failed>`，不影响投递 |
| **2** | 按 **pid** 分文件 | 无 | — | guest 进程会重启，不同 pid 的日志不能混（否则无法按次分析） |
| **3** | 全量落盘（不采样） | 无 | 🟡 需观察 | 高频启动场景可能产生较大文件；目前只用于排查，可接受 |

## 约束与坑

1. **绝不能抛异常**：观察点位于 Activity 投递主链路，抛一次就可能让 Activity 起不来。
   现有实现所有分支都 try/catch，新增字段解析时**必须保持**这个约定。
2. **写入必须加锁**（`writeLock`）：主线程与其他线程都可能写，否则行会交错、日志不可读。
3. **文件名带 pid**，读日志时要按 pid 找最新那个。
4. 这里只写"见证"，不要在这里做任何"基于日志内容的行为决策"（否则日志就成了逻辑的一部分，违反单一职责）。
5. 与 v146 起的 `DebugTrace` 区别：`DebugTrace` 是**统一调试出口**（会进 `debug-trace.log`），
   这里是**针对 startActivity 链路的专用 witness 文件**，保留是因为它的格式与服务端排查脚本约定过。
