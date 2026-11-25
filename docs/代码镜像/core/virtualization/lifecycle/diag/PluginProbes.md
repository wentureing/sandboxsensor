# PluginProbes（core:virtualization / lifecycle/diag）

- 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/diag/PluginProbes.kt`
- 测试：`core/virtualization/src/test/java/com/nextvm/core/virtualization/lifecycle/diag/PluginProbesTest.kt`（P1–P5）
- 来源：从 `VirtualApplicationManager` 抽出（v116–v123 插件框架探针，B4-3a）
- 日期：2026-11-25

## 职责

**负责**：对 guest 的插件框架（`com.mc10086.plugin.core.PluginLoader`）做**只读**观测，
把状态写进 breadcrumb：是否初始化、`initLoader` 签名、静态字段、App Startup meta-data、
AW（SecNeo）delegate 状态、以及触发内存 dex 转储。

**不负责**：

| 不负责 | 在哪 |
| --- | --- |
| 何时调用（bind 结束、第一个 Activity、t30/t60/t120） | 调用方：`VirtualApplicationManager`（bindApplication / 生命周期回调） |
| 修改 guest 状态（**永不调用 `initLoader`**） | 本类只读；`getApplication()` 视为纯 getter |
| 日志通道 | `GuestBreadcrumb`（本类只产出调用） |
| 内存 dex 转储实现 | `GuestMemoryDump.dumpMemoryDex`（B4-1） |

**一句话**：本类回答"插件的加载器现在是什么状态、为什么没初始化"，不回答"什么时候测、测完怎么用"。

## 函数

| 函数 | 签名 | 作用 / 关键前提 |
| --- | --- | --- |
| `probePluginLoaderState` | `(application: Application, record: ApplicationRecord)` | v116/v117/v123/v118/v119/v120/v121/v122 一串只读探针。**副作用**：注册 t30/t60/t120 主线程延迟重查；对 AW 壳会启动后台线程做 dex 转储 |
| `probePluginStatePhase` | `(application, record?, phase)` | v118：首 Activity 后重查 `getApplication()` 状态；`record` 可为空 |
| `renderFieldValue` | `(v: Any?): String` | **纯函数**：`null`/`Application`/基础类型/数组长度/其它（只类名，**不调 toString**） |
| `classifyMetaData` | `(entries: List<Pair<String,String?>>): MetaDataClasses` | **纯函数**：`value == "androidx.startup"` → startups；`key` 含 `plugin`（忽略大小写）→ pluginKeys |

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | 硬编码 `com.mc10086.plugin.core.PluginLoader` 与 `com.secneo.apkwrapper.AW` | 这是**定向对抗**（CM 加固壳），不是通用探针 | 必要偏差 | `v116/v118/v121` 日志行 |
| 2 | 静态字段值只按类型渲染（`obj(<类名>)`），从不 `toString()` | 读 guest 对象的 `toString` 会执行 guest 代码，可能崩/改状态 | 必要偏差 | `v123-plugin-probe` 行 |
| 3 | 探针内部注册 t30/t60/t120 定时器 | "首 Activity 后重查"需要延迟；属于探针自身的调度，不属于启动链控制流 | 中性偏差（随实现搬迁） | `v120-plugin-probe timed re-checks scheduled` |
| 4 | 对 AW 壳在 `probePluginLoaderState` 内启动 dex 转储线程 | v121 需要进程内内存（磁盘 dex 只有壳的 7 个类） | 必要偏差 | `v121-plugin-probe memdex ...` |

## 约束与坑

1. **绝不调用 `initLoader`**：一旦调用就改变了被测量状态，所有日志都失去意义。
2. **所有反射都要 catch**：探针失败不能影响 bind（外层 `try/catch(_: Throwable)` 覆盖整段）。
3. **`getApplication()` 必须判静态**：非静态时调 `invoke(null)` 会抛，日志里要体现 `getApplication is not static, skipped`。
4. **`record` 在 `probePluginStatePhase` 可以为 null**（首 Activity 时可能还没绑定）→ 日志里 `contentProviders=null` 是合法输出。
5. **两个入口不可纯 JVM 测**：依赖 `Application`/`ApplicationRecord`/反射；可测部分已抽成 `renderFieldValue` / `classifyMetaData`（P1–P5）。
6. **日志前缀是排查契约**：`v116/v117/v118/v119/v120/v121/v122/v123-plugin-probe` 必须逐字保持（现有 grep 依赖）。
