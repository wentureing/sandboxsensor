# GuestBindBackgroundTasks

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/startup/GuestBindBackgroundTasks.kt`
> 来源：B4-4c 把 `VirtualApplicationManager.bindApplication` 里**两条路径各写一遍**的 4 行后台任务收成一处（2026-11-26）

## 职责

**只回答一个问题：bind 成功之后，要按什么顺序启动 4 个后台观察任务。**

- 负责：`DexMemDump`（内存 dex 转储）→ `StateProbe`（状态快照）→ `WebViewWatch`（WebView 观察）
  → `WebViewWarmup`（WebView 预热），顺序固定。
- **不负责**：`scheduleGuestStackDumps` / `scheduleHijackTrace`（那两个是 VAM 私有、依赖更多状态，
  随 B4-4d 一起搬）；也不负责 `Application.onCreate` 之前的任何事。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `scheduleObservers(packageName: String, filesDir: File)` | 按固定顺序调度 4 个后台任务 | 每个任务内部都自带 try/catch 与延迟；本函数**不阻塞**、失败不抛；要求 `filesDir` 是 **guest** 的 files 目录 |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 由"两处 4 行"变成"一处 1 行 ×2 调用" | 原 framework (`VAM:252-255`) 与 legacy (`VAM:345-348`) 各写一遍，逐字相同 | **必要偏差**（消除重复，规则 18.2） | 两次 golden（framework / legacy）里 `v130-watchdog` 之后的调度顺序不变；C6/C7 仍绿 |

## 约束与坑

- **顺序不能改**：`WebViewWarmup` 会主动预热 `WebViewFactory`，所以它必须排在最后（前面几个只读观察）。
  这也是 VAM 里"`patchFrameworkDataDir` 必须早于 `registerBreadcrumbCallbacks`"那条约束的下游 ——
  一旦 Warmup 提前，provider 看到的 context 会被固化。
- **`filesDir` 必须是 guest 的**（`application.filesDir` / `fwApp.filesDir`），不是 `virtualContext.filesDir`
  以外的东西；传错会让 dump/快照落到宿主目录。
- 本函数**不做去重**：`bindApplication` 保证一次 bind 只调用一次（契约 C10 固定 `bind complete` 只 1 次）。
- 这 4 个任务都是"延迟/后台"语义（`R3 异步产出型`），所以调用它们**不产生** breadcrumb 事件；
  想验证是否跑起来要看各自的 `<name>-<pid>.log`（`stackdump-*` / `webviewwarm-*` 等）。
