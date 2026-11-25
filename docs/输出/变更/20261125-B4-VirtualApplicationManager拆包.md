# 变更：B4-1 拆 VirtualApplicationManager（诊断转储两个职责）

- 日期：2026-11-25
- 状态：**执行中**（分支 `split/b4-diag-dump`，待 `module-test :core:virtualization`）
- 依据：`docs/输出/调研/调研-高频修改热点与测试优先级.md`、`docs/计划/计划-大文件拆包与职责导出.md`
- 粒度：**两个职责**（用户 2026-11-25：Java/Kotlin 授权放松，但仍保持每批两个职责）

---

## 一、为什么拆这个文件

`VirtualApplicationManager.kt` = **4 589 行**（文档旧值 4 427 已过时），其中诊断/探针类函数约 **2 400 行（>50%）**。
读启动链时要在探针里翻——这是"排查故障不方便"的真正来源，不是行数本身。

本批先搬**纯转储**（无启动链控制流、无业务副作用），风险最低。

## 二、本批范围（两个职责）

| 职责 | 函数 | 行数 | 去向 |
| --- | --- | --- | --- |
| **① 线程栈转储** | `dumpGuestStacks(out, label)` | 61 | `lifecycle/diag/GuestMemoryDump.kt` |
| **② 内存 dex 转储**（含工具 `le32`） | `dumpMemoryDex(application)` + `le32(b, off)` | 205 + 6 | 同上 |

调用点只改两处：`VirtualApplicationManager:970`（watchdog 里）与 `:2584`（plugin probe 里）。

## 三、行为等价论据

| # | 面 | 判据 |
| --- | --- | --- |
| 1 | 代码**逐字搬运** | 脚本按行号区间抽取（977-1037 / 2757-2961 / 2963-2968），只改 `private fun` → `fun`（object 内可见性）与缩进 |
| 2 | `DiagSuspendGate` 串行化 | 原逻辑保留（`synchronized(DiagSuspendGate.lock)`）；新增 `import ...DiagSuspendGate` |
| 3 | 输出文件与格式 | `stackdump-<pid>.log` 内容格式不变（`==== label ====` + 主队列 + 线程栈）；`memdex-<pid>-<n>.dex` 命名与校验条件不变 |
| 4 | 调度时机 | **不动**：watchdog / plugin probe 仍在原处调用，只换成 `GuestMemoryDump.xxx(...)` |
| 5 | debug 门禁 | 本类不引入任何新日志；`GuestBreadcrumb` 调用原样 |

## 四、影响范围

| 文件 | 类型 | 变化 |
| --- | --- | --- |
| `core/virtualization/.../lifecycle/diag/GuestMemoryDump.kt` | **新增** | 290 行（object 内三个函数） |
| `core/virtualization/.../lifecycle/VirtualApplicationManager.kt` | 修改 | −272 行；2 个调用点改为 `GuestMemoryDump.xxx`；新增 1 个 import |
| `core/virtualization/src/test/.../diag/GuestMemoryDumpTest.kt` | **新增** | G1–G5（`le32`，纯 JVM） |
| `docs/代码镜像/core/virtualization/lifecycle/diag/GuestMemoryDump.md` | **新增** | 规则 7 四节（含 `DiagSuspendGate` 的坑） |

## 五、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 搬漏 import（如 `DiagSuspendGate`） | `check-imports` 立刻报出（本次已命中并修）；CI 编译兜底 |
| 2 | 反射字段在 Android 版本变化时失效 | 原逻辑已 try/catch 降级；行为未改 |
| 3 | 转储内容变化 | 逐字搬运 + 镜像文档记录输出格式；真机 smoke 对比 `stackdump-*` 首行 |
| 4 | 与后续批次（`dumpRealMaps`）冲突 | `dumpRealMaps` 依赖 `RealSnap`，本批不动它 |

## 六、回退

```powershell
git -C _ref/ShitJumpingVM checkout baseline-v150
git -C _ref/ShitJumpingVM branch -D split/b4-diag-dump
```

## 七、验证

| 项 | 判据 |
| --- | --- |
| 静态 | `kotlin-lint`（23 文件）+ `check-imports`（仅 1 处既有误报） |
| 单测 | `module-test :core:virtualization`（含 G1–G5 + 既有 20 例） |
| 真机 | 装包后 `stackdump-<pid>.log` 仍生成、首行 `==== <label> ====`；`memdex-*` 行为不变 |
| 行数 | `VirtualApplicationManager` 4 589 → 约 4 317 |

## 八、后续批次（未做）

| 批 | 内容 | 说明 |
| --- | --- | --- |
| B4-2 | `dumpRealMaps`（378 行）+ `RealSnap` 相关 | 依赖快照类型，需一并搬 |
| B4-3 | plugin / login / autofill / auth-intent 探针（≈1 400 行） | 需参数化 `application`/`activity` |
| B4-4 | 启动链：`bindApplication` / `createApplication` / `initContentProviders` | 最高风险，先补 breadcrumb 契约测试 |
