# 已完成计划归档（2026-11）

这些计划**已落地或已被替代**，保留仅供追溯。当前计划见 `docs/计划/`。

| 文件 | 状态 | 替代/证据 |
| --- | --- | --- |
| `已完成-P0P1.md` | ✅ 已完成 | P0/P1 各项已合入 baseline |
| `P0-3-主进程去hook.md` | ✅ 已完成 | 容器主进程不装 hook（`initNativeHooks skipped: container main process`） |
| `P0-4-P0-5-隐藏容器.md` | ✅ 已完成 | 落实在 stub/命名与诊断路径过滤 |
| `P1-1-SelfCheckRunner.md` | ⛔ 已被替代 | 实际实现为 `diag/ContainerSelfCheck`（`CmdReceiver SELF_CHECK`，6 项主动断言） |
| `计划-路径规则抽成纯Kotlin模块.md` | ✅ 已完成 | `core/model/PathKinds.kt` + `PathRuleStore.kt` |
| `计划-路径转换重构-表驱动精简.md` | ✅ 已完成 | 14 族数据表 + ROOT_FORM 语义（Java/C++ 同步） |
