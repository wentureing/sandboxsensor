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
| `计划-拆分顺序评估.md` | ✅ 已合并 | 结论并入 `docs/计划/计划-拆分进度与顺序.md`（VAM 诊断块已拆完，下一步 B4-4） |
| `计划-拆分执行清单.md` | ✅ 已合并 | 同上；v148–v157 的已完成清单迁入该文档 |
| `计划-读写验证与两个测试模块.md` | ✅ 已完成 | 两个测试模块已建（framework 26 例 + 容器侧测试），探针 W/F 系列已落地 |
