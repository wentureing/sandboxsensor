# HijackProbes（core:virtualization / diag/probe）

- 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/diag/probe/HijackProbes.kt`
- 测试：`core/virtualization/src/test/java/com/nextvm/core/virtualization/diag/probe/HijackProbesTest.kt`（H1–H2）
- 来源：从 `VirtualApplicationManager` 抽出（v35/v36 劫持观测，B4-3b）
- 日期：2026-11-25

## 职责

**负责**：只读采样 guest 进程的劫持状态，写 `hijacktrace-<label>.log` 与 `Timber(HijackTrace)`：
`ActivityThread.mInstrumentation`、lifecycleCallbacks 列表、`HijackSDK` 标志位、
`ProxyInstrumentation` 实例与原始 Instrumentation、每个 Activity 的 `mInstrumentation`、
框架 provider 表、`libUnicomFPSDK.so` / `libDexHelper.so` 是否已加载。

**不负责**：

| 不负责 | 在哪 |
| --- | --- |
| 何时采样（+N 秒时间表、stock-init 实验状态） | `VirtualApplicationManager` 的 `scheduleHijackTrace` / `swapInStockInstrumentation` |
| 日志通道 | `Timber` + 文件（本类只 append） |
| 判断"劫持是否成功" | 由读日志的人判断（本类不下结论） |

## 函数

| 函数 | 签名 | 作用 / 关键前提 |
| --- | --- | --- |
| `describe` | `(name: String?): String` | **纯函数**：null → `"null"`，否则原样 |
| `sample` | `(out: File, label: String, stockInitNote: String)` | 只读采样一次；内部所有反射都包在 try/catch 里，失败只写日志行 |
| `unwrap` | `(out: File, label: String, stockInitExperiment: Boolean, unwrapEnabled: Boolean)` | 把 `mInstrumentation` 从 `ProxyInstrumentation` 还原成 `oriInstrumentation`；两个开关任一不满足就只写"skipped" |

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | 硬编码 `com.sdk.hijack.api.HijackSDK` / `com.sdk.hijack.base.ProxyInstrumentation` / `libUnicomFPSDK.so` | 定向排查特定加固/风控 SDK | 必要偏差 | 日志行里的 `HijackSDK.*` / `maps ... loaded` |
| 2 | `unwrap` 默认关闭（`unwrapEnabled=false`） | v36 observe-only baseline：先看清状态再改 | 必要偏差 | `unwrap disabled (v36 observe-only baseline)` |
| 3 | 用 `fieldOf`（沿父类查找）而不是 `getDeclaredField` | 与 `VirtualApplicationManager` 原实现一致；继承字段也能取到 | 必要偏差（已统一到 `diag/Reflect.kt`） | 取不到时抛 `NoSuchFieldException`，被 catch 成一行日志 |

## 约束与坑

1. **只读**：`sample` 不修改任何状态；`unwrap` 是唯一会写 `mInstrumentation` 的函数，且默认禁用。
2. **不能影响 guest**：所有反射失败都只落日志行（`<异常类名>`），不抛到调用方。
3. **`stockInitNote` 由调用方传入**：它是 stock-init 实验的状态描述，本类不持有该状态。
4. **日志前缀固定**：`==== <label> ====`、`mInstrumentation = ...`、`maps ... loaded = ...`，现有排查靠 grep。
5. 两个入口都不可纯 JVM 测（依赖 `ActivityThread`）；可测部分已抽成 `describe`（H1–H2）。
