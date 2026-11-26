# ContentProviderInitializer

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/startup/ContentProviderInitializer.kt`
> 来源：B4-4a 从 `VirtualApplicationManager.initContentProviders` 原样搬出（2026-11-26）

## 职责

**只回答一个问题：按什么顺序、用什么容错把 guest 的 ContentProvider attach 起来。**

- 负责：逐个 `classLoader.loadClass` → 无参构造 → 组 `ProviderInfo`（authority / metaData / applicationInfo）
  → `attachInfo(guest Application, info)` → 注册到 `ActivityManagerProxy`；
  跳过 ShizukuProvider；四类异常各自吞掉并记 breadcrumb。
- **不负责**：Application 的创建与 attach、`onCreate` 调用、记录归属（`ApplicationRecord.contentProviders`
  由调用方在 `onInstalled` 回调里维护）、provider 的运行时调用（那是 AMS 代理的事）。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `isShizukuProvider(className: String, authority: String): Boolean` | 三条判据（类名/`rikka.shizuku`/authority，均忽略大小写） | 纯函数 |
| `install(instanceId, providers, authorities, classLoader, virtualContext, application, providerMetaData = emptyMap(), installedInto: MutableList<ContentProvider>? = null)` | 逐 provider attach | 调用前 `application` 必须已 attach 完（AOSP 要求 provider 早于 `Application.onCreate`）；每装好一个就 `installedInto?.add(provider)` |

**等价性结论（规则 10）**：忽略**下面两处已登记偏差**后，`install` 与旧 `initContentProviders`
**逐字等价（1 245 字符）**：

```powershell
node tools/lint/check-refactor-equivalence.mjs --old-file tools/tmp/vam-old.kt --old-fn initContentProviders `
  --new-file <ContentProviderInitializer.kt> --new-fn install `
  --ignore "val record = applications|shizuku|ShizukuProvider|^\s*\) \{|continue|record\?\.contentProviders|installedInto"
```

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| `record?.contentProviders?.add(provider)` → `installedInto?.add(provider)` | 原实现直接读 VAM 私有字段；搬出后改为**同名列表注入**，VAM 传的正是 `record?.contentProviders`（`record` 仍在委托前取一次） | **必要偏差**（结构，语义等价）——规则 10 允许的"回调/参数注入"类改造 | `BreadcrumbGoldenContractTest` C3/C5；`record` 取值时点不变 |
| 三条内联 `contains` → `isShizukuProvider(...)` | 判据抽成公开纯函数以便单测，三条判据与其 `ignoreCase` 全部保留 | 必要偏差（可测性），语义等价 | `ContentProviderInitializerTest` P1–P6 |
| 参数用 `MutableList<ContentProvider>?` 而不是函数类型 | 函数类型参数的 `->` 会破坏 `check-refactor-equivalence.mjs` 的花括号/尖括号配对（实测：提取失败，判成不等价） | 必要偏差（工具可验证性） | 上面的等价命令可复现 |

## 约束与坑

- **顺序是契约**：`providers` 的遍历顺序 = manifest 声明顺序，不得重排，也不得并行
  （AndroidX Startup / WorkManager / Firebase 之间有隐式依赖）。契约 C5 固定"全部 attach 完才进 onCreate"。
- **四类异常必须分开 catch**：`ClassNotFoundException` / `SecurityException` / `IllegalStateException` /
  `Exception` 打的是**不同** breadcrumb 文案（`provider NOT FOUND` / `... SWALLOWED` / `provider FAILED (bind continues)`），
  合并会破坏"失败降级"的可观测性（契约 C5 断言 `(bind continues)` 存在）。
- `attachInfo` 第一个参数必须传 **guest Application**（不是 `ContextImpl`）：有 provider 会把
  `getContext()` 强转 `Application`（如 SoulPermission 的 `InitProvider`），传错会静默失效。
- authority 为空时回退 `${virtualContext.packageName}.<简单类名>`——这是**容器自造**的补位规则，
  AOSP 里 authority 来自 manifest；改它会改变 provider 的寻址（契约与探针 J 系列都看这条）。
- Shizuku 判据是"容器原理决定"（跨用户权限拿不到），属**必要偏差**，不要"顺手宽松化"。
