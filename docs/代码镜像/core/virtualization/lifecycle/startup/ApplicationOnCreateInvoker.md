# ApplicationOnCreateInvoker

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/startup/ApplicationOnCreateInvoker.kt`
> 来源：B4-4b 从 `VirtualApplicationManager.callApplicationOnCreate` 原样搬出（2026-11-26）

## 职责

**只回答一个问题：怎么触发 guest 的 `Application.onCreate()`，以及失败时怎么降级。**

- 负责：先走 `Instrumentation.callApplicationOnCreate()`（与 AOSP 一致）；抛异常时判断是否
  "Shizuku 缺跨用户权限"→ 吞掉；否则直接调 `application.onCreate()`；再失败则照常抛出。
- **不负责**：`attach`/`attachBaseContext`、Provider 安装、实例状态（`ApplicationRecord.state`）、
  `onCreate` 之后的事（`PluginProbes`、watchdog）——那些在 VAM 里。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `isShizukuSecurityException(t: Throwable?): Boolean` | 判"是不是 Shizuku 触发的 `SecurityException`"（取 `cause.cause ?: cause ?: t`，再看三条消息关键字，忽略大小写） | 纯函数；`message == null` 时返回 `false` |
| `invoke(application: Application)` | 触发 onCreate，含两级降级 | 会写 breadcrumb（enter / exit ok / SWALLOWED / FAILED）；**非 Shizuku 异常一律向上抛** |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 抽出 `isShizukuSecurityException` | 原实现把"取 cause + 三条 `contains`"**复制了两遍**（Instrumentation 失败一次、直接调用失败一次）。抽出后两处共用一份判据，且变成可纯 JVM 测的纯函数 | **必要偏差**（可测性 + 消除重复，规则 18.2） | `ApplicationOnCreateInvokerTest` A1–A8；等价检查忽略该判据后 785 字符逐字相同 |
| 位置从 VAM 私有方法变为 object 方法 | 调用点仍是 VAM 的 `callApplicationOnCreate`（薄委托） | 中性偏差 | — |

**等价性结论（规则 10）**：

```powershell
git -C _ref/ShitJumpingVM show <上一提交>:<VAM 路径> > tools/tmp/vam-old2.kt
node tools/lint/check-refactor-equivalence.mjs --old-file tools/tmp/vam-old2.kt --old-fn callApplicationOnCreate `
  --new-file <ApplicationOnCreateInvoker.kt> --new-fn invoke `
  --ignore "val cause|isShizukuSecurityException|cause\.message|cause2\.message|if \(cause2? is SecurityException|^\s*\) \{|^\s*\(cause2?"
```

## 约束与坑

- **吞异常的边界不能放宽**：只吞 `SecurityException` 且消息命中 Shizuku 关键字；其他异常必须抛出，
  否则 guest 的 `onCreate` 崩溃会被静默掩盖（`Breadcrumb` 里会少一次 `FAILED`）。
- `Instrumentation()` 直接 `new` 而不是取 `ActivityThread.mInstrumentation`：AOSP 里
  `callApplicationOnCreate` 只是转调 `app.onCreate()`，用新实例即可；换成真实 Instrumentation 会
  引入 guest 与宿主 Instrumentation 混用的风险。
- breadcrumb 文案是排查入口（`Application.onCreate enter/exit ok/exit ok (direct)`），不可改：
  契约 C5/C8 与"启动到哪一步"的判断都按这些文案。
- 本函数**不检查** `application` 是否已 attach —— 调用方（VAM `bindApplication`）保证顺序。
