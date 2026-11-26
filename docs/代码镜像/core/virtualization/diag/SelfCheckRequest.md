# SelfCheckRequest（core/virtualization/diag/SelfCheckRequest.kt）

## 职责

**负责**：guest 侧自检的**请求文件协议**——主进程写 `<guestFiles>/selfcheck.request`，guest 进程首次 resume 时消费（删除后再跑）。

**不负责**：自检本身（`ContainerSelfCheck`）、触发时机（`VirtualApplicationManager` 的 resume 回调）、debug 门控（调用方各自判断）。

## 函数

| 签名 | 作用 | 副作用 |
| --- | --- | --- |
| `fileOf(dir): File` | 请求文件路径 | — |
| `isPending(dir): Boolean` | 是否有未消费请求 | 只读 |
| `request(dir): Boolean` | 写时间戳作为请求 | `mkdirs` + 覆盖写；失败返回 false，不抛 |
| `consume(dir): Boolean` | 删除请求文件 | **删成功才返回 true**（保证一次请求只跑一次） |

## 偏差登记

| 偏差 | 为什么 | 类型 | 观测 |
| --- | --- | --- | --- |
| 用文件而不是广播 | guest 与容器同 uid 同包，动态广播会同时投递两个进程，需要额外进程判定；文件天然"只被目标目录的进程看到" | 必要偏差 | `ls <guestFiles>/selfcheck.request` |
| `consume` 先删后跑 | 若先跑后删，自检崩溃会导致每次 resume 重跑；先删最多丢一次 | 必要偏差 | 请求文件只出现一次 |

## 约束与坑

1. **`consume` 必须在自检执行前调用**（先删后跑），否则崩溃会反复重跑、拖慢每次 resume。
2. 调用方必须自己加 **debug 门控**（`DebugFlags.isDebugBuild()`）——本对象不做门控，便于单测。
3. 文件内容（时间戳）只用于事后判断"哪次触发"，不参与判定。
4. 不要把请求文件放进 `virtualRoot`（用户可见）——它属于 guest 的 `filesDir`。
