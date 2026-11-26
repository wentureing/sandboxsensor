# GuestSelfCheck（core/virtualization/diag/GuestSelfCheck.kt）

## 职责

**负责**：在 **guest 进程内**跑一遍 `ContainerSelfCheck`——用这个进程自己的规则表（S7 真检）、自己的 `debug-trace.log`（S8 真检）、自己的 `cacheDir`（S3 写入目标），结果写 `checks.log` 的 `S guestselfcheck …`。

**不负责**：何时跑（`VirtualApplicationManager` 首次 resume 消费请求）、请求协议（`SelfCheckRequest`）、debug 门控（本类只做一次防御性判断）。

## 函数

| 签名 | 作用 | 前提 |
| --- | --- | --- |
| `run(nativeHookBridge, app)` | 组装 `PathProbe` → 跑 8 项 → 写 `checks.log` + breadcrumb | `app` 为 guest Application；非 debug 直接返回 |

## 偏差登记

| 偏差 | 为什么 | 类型 | 观测 |
| --- | --- | --- | --- |
| `confText = null` + `confRequired = false`（S6 记 skipped） | guest 侧读 `path_spoof.conf`（容器主进程 files）会被路径翻译到沙箱，读不到 | 必要偏差 | `S guestselfcheck.S6 ok skipped(...)` |
| 写 guest 自己的 `cacheDir/selfcheck` | 与主进程同规则：自检不留痕迹、不进 `virtualRoot` | 必要偏差 | `S3` 的 `target=.../cache/selfcheck/...` |
| `javaIoStats/installed` 包一层 `JavaIoStats` 私有对象 | 便于将来在测试里替换静态依赖 | 中性偏差 | — |

## 约束与坑

1. **必须在 guest 进程内调用**：放到主进程会退化成"规则表为空 → S7 skipped"，失去意义。
2. **只在首次 resume 跑一次**：`VirtualApplicationManager.guestSelfCheckDone` 门闩 + `consume` 先删后跑，双重保证。
3. **S7 现在应当是"真检"**：guest 进程规则表非空，`S guestselfcheck` 里 S7 应为 `ok`（带 `rules=N from=...`）。如果仍是 `skipped`，说明规则没注册到该进程（v142 那类问题）。
4. 结果落两处：`checks.log`（可 `COLLECT_OUT` 拉走）+ breadcrumb（`guestselfcheck …`，便于 logcat 观察）。
