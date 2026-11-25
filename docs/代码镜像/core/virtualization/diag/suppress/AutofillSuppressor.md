# AutofillSuppressor（core:virtualization / diag/suppress）

- 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/diag/suppress/AutofillSuppressor.kt`
- 测试：`core/virtualization/src/test/java/com/nextvm/core/virtualization/diag/suppress/AutofillSuppressorTest.kt`（A1–A3）
- 来源：从 `VirtualApplicationManager` 抽出（v129/v131 autofill 抑制，B4-3b）
- 日期：2026-11-25

## 职责

**负责**：阻止 guest 窗口触发 Autofill 会话，并报告视图的 autofill 模式分布。

**不负责**：

| 不负责 | 在哪 |
| --- | --- |
| 何时调用（resumed / v124 timer） | `VirtualApplicationManager` 的生命周期回调 |
| 判断"是否该抑制" | 一直抑制（容器内 autofill 服务不可用） |
| 日志通道 | `GuestBreadcrumb` |

**为什么叫 Suppressor 而不是 Probe**：它**修改**每个 View 的 `importantForAutofill`，
不是只读观测。命名按实际行为。

## 函数

| 函数 | 签名 | 作用 / 关键前提 |
| --- | --- | --- |
| `formatModes` | `(Map<Int,Int>): String` | **纯函数**：渲染成日志里的 `8:1,0:3` |
| `formatExplicit` | `(List<String>): String` | **纯函数**：用 `;` 拼接（条数上限由调用方控制在 10） |
| `disableForActivity` | `(Activity)` | v129：decorView 上设 `NO_EXCLUDE_DESCENDANTS`，子 View 继承 |
| `disablePerView` | `(Activity)` | v131：遍历视图树逐 View 设置，并输出 total/changed/modes/explicit |

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | 无条件把所有 View 改成 `NO_EXCLUDE_DESCENDANTS` | v129 的 decor 级 flag 实测仍会 startSession（launch158）；容器内 autofill 服务不会应答 → 5s ANR | 必要偏差 | `v131-autofill per-view ... changed=N` |
| 2 | `explicit` 最多留 10 条 | 只用于判断"是否有字段显式开了 autofill"，不需要全量 | 中性偏差 | `explicit=` 长度 |
| 3 | 失败静默（try/catch 空） | 不能因为 autofill 打断 Activity 生命周期 | 必要偏差 | 无（失败即跳过） |

## 约束与坑

1. **必须在主线程调用**（遍历 View 树）。
2. **只在 guest Activity 上调用**：呼宿主 Activity 会改到容器自己的输入框。
3. 失败路径全静默：`decorView` 为空（内容未创建）时直接返回，等 resumed 再覆盖。
4. `formatModes` 的 key 是 `View.IMPORTANT_FOR_AUTOFILL_*` 常量值（0/1/2/8），顺序取决于 map 迭代顺序。
5. 入口不可纯 JVM 测（依赖 Activity/View）；可测部分为 `formatModes` / `formatExplicit`（A1–A3）。
