# ActivityThreadApplicationPatch

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/startup/ActivityThreadApplicationPatch.kt`
> 来源：B4-4c 从 `VirtualApplicationManager.patchActivityThreadApplication` 原样搬出（2026-11-26）

## 职责

**只回答一个问题：把 `ActivityThread` 里指向宿主的 Application 引用改成 guest 的。**

- 负责：反射拿 `ActivityThread` 实例 → 把 `mInitialApplication` 设为 guest Application →
  （尽力）把 `mBoundApplication.appContext` 也设为 guest → 记一条 `v138-appctx` 日志。
- **不负责**：`LoadedApk.mApplication` 的补丁（在 VAM 的 `patchLoadedApkApplication`）、
  路径补丁、provider 初始化、`onCreate` 调用。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `isEnabled(): Boolean` | 属性开关：`nextvm.webviewAppContext != "0"` 才启用 | 纯函数（读 system property）；**不是 parseBoolean** |
| `apply(application: Application)` | 反射改写 ActivityThread 的两个字段 | 全程 `try/catch` + breadcrumb；**失败绝不抛**（bind 不能因此中断）；要求 `currentActivityThread()` 非空 |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 抽出 `isEnabled()` | 原实现是函数内联的 `val enabled = try { … } catch { true }`；抽出后成为可测纯函数 | **必要偏差**（可测性），语义等价 | `ActivityThreadApplicationPatchTest` B1–B4；等价检查忽略该判据后 **882 字符逐字相同** |
| 位置从 VAM 私有方法变为 object 方法 | 调用点仍是 VAM 的薄委托，两条 bind 路径各调一次 | 中性偏差 | golden C6 |

## 约束与坑

- **两条 bind 路径都调它，但结果不同**：framework 下 `mInitialApplication` 原本就是宿主（`-> com.nextvm.app`），
  legacy 下会被改成 guest（`-> mark.via`）。这是 `STATUS.md` §2 #9 / 缺陷计划 **L5**，属未定论行为，
  **重构期间不要"顺手对齐"**。
- `mBoundApplication` 的改写包在**内层** try/catch，失败只影响 `bindInfo` 文本（`failed: XxxException`），
  不影响 `mInitialApplication` 的改写 —— 不要合并成一个 try，否则一处失败会连坐。
- `fieldOf` 会沿父类查找；`mBoundApplication` 的 `appContext` 字段来自 `ActivityThread.AppBindData`（私有内部类），
  所以要用 `bindData.javaClass` 而不是写死类名。
- 日志文案 `v138-appctx mInitialApplication <before> -> <after> ; mBoundApplication.appContext <info>`
  是 golden C6 顺序与排查入口，改文案会让契约与人工排查口径同时失效。
- `nextvm.webviewAppContext` 是 **system property**（不是 `debug.nextvm.*`），因此 release 包也能关；
  这是上游既有约定，不要擅自改成 `DebugFlags`（那会被 R8 在 release 中删除）。
