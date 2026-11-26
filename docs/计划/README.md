# 计划（`docs/计划/`）

> 回答：**接下来做什么、按什么顺序做。**
> 写作模板（`AGENTS.md` 硬要求）：目标 / 影响范围 / diff 预览 / 风险 / 回退 / 验证方法。

---

## 一、当前在办（2026-11-26）

| 优先 | 事项 | 状态 | 文档 |
| --- | --- | --- | --- |
| **B4-4（当前主线）** | **启动链拆分**：VAM 的 `bindApplication` / `createApplication` / `initContentProviders` / LoadedApk 注入 / `bindViaFrameworkLoadedApk`（~700 行，VAM 现 2350 行） | **前置已完成**：4 份真机 golden + 11 条契约；下一步=定拆分边界（阶段表，每阶段一个类） | `计划-拆分进度与顺序.md` §1.3 / §二、`../架构/架构-启动链契约.md` |
| B5（其后） | `ActivityThreadHook` 核心块（~950 行） | 等 B4-4 的契约方法成熟后再做 | `计划-ActivityThreadHook与Binder语义重构.md` |
| ⏸ **C++** | `native-hook.cpp`（7471 行）整文件拆 TU / 继续抽纯逻辑 | **用户 2026-11-26：不要轻易修改 C++** → 暂停，不再作为候选 | `计划-大文件拆包与职责导出.md` §四（B2 行） |
| **登记待修** | **启动链已知缺陷 10 条**（两条 bind 路径不一致 4 条 + session 级 2 条 + 早期窗口/探针 3 条 + 中性 1 条） | **只登记，重构完成后统一修**（修序与判据已定） | `计划-启动链已知缺陷（重构后修复）.md` |
| P0 | 语义等价重构清单（后续批次） | 📋 待逐项执行 | `计划-语义等价重构清单.md` |
| P1 | 重新评估「假 dataDir 让 WebView 走标准分支」 | ❌ **已否证**（dataDir 归属不是 MIME 变量） | `../输出/记录/记录-假dataDir假设被否证.md` |
| P2 | 补齐注入位置（`setupPathFidelityRedirections` / `registerVirtualSdcard` / `installDeferredProxies`） | 📋 待验证 | `../架构/架构-注入时机审计.md` |
| ✅ 完成 | Java 层文件 I/O 重定向语义等价重构（v148：ErrnoException/EINTR/引用缓存 + 拆分 JavaIoHooks） | 已实现，待真实应用回归 | `../输出/记录/记录-JavaIoHook导致Via不可交互.md`、`../代码镜像/core/hook/JavaIoHooks.md` |

## 二、待执行（按优先级）

| 优先 | 篇 | 工作量 | 风险 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | `P0-4-P0-5-隐藏容器.md` | 1.5 天 | 中–高 | 待做（含 `readdir` 过滤） |
| 2 | `计划-三问回答-异常率与开关与缺陷.md` | — | — | 待做（异常率 / 开关 / 缺陷三问的答复与拆解） |
| 3 | `P1-1-SelfCheckRunner.md` | 1.5 天 | 中 | 待做 |
| 4 | `P2-长期重构.md` | 2–3 天/项 | 中高 | 待做 |
| 5 | `验收与流程.md` | — | — | 验收标准与发布流程 |

## 三、已完成（保留作参考）

| 篇 | 内容 |
| --- | --- |
| `已完成-P0P1.md` | P0-1 `DiagnosticsSink`、P0-2 `probe-out`、P1-2 `HostReadPolicy` |
| `P0-3-主进程去hook.md` | **已落地**（容器 `b4e85a4` + 修复 `de50a82`/`1631541`）：主进程零 hook，`is_import_read_exempt` 整条机制删除 |
| `计划-WebView修复.md` | WebView 修复的原计划（**注意**：其中的方案 A/B/C 分支已被实测修正，现行结论见 `../输出/记录/记录-W2-text-plain机制收敛.md`） |

## 四、已归档（结论被推翻，勿再按它执行）

| 文档 | 原因 |
| --- | --- |
| `P1-3-WebView代读.md` | 前提"等 Gaia 机制研究"已消失（Gaia 靠路径保真，不需要代读）→ 见 `../archive/错误结论-2026-11/` |

## 五、明确放弃

| 项 | 原因 |
| --- | --- |
| `:probe` 独立进程 | guest 进程本身就是全 hook 环境，测试直接跑那里 |
| 删除 `syscall`/`fork`/`mmap`/`mprotect` 的 hook | 只打日志 ≠ 无用途；改为加 `NS_DIAG_ONLY` 开关而非删除 |

## 六、约束

1. 所有代码改动走 `../输出/变更/` 流程（落盘计划 → 等确认 → 改 → 追加实际 diff）
2. **一次只 dispatch 一个编译 CI，明确指定容器或探针**（见 `../../AGENTS.md`）
3. 计划里引用"已验证"的结论时，必须指向 `../输出/记录/` 里的实测证据，不能只引计划本身
