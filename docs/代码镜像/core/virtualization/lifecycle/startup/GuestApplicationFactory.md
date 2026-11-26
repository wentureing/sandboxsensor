# GuestApplicationFactory

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/startup/GuestApplicationFactory.kt`
> 来源：B4-4a 从 `VirtualApplicationManager.createApplication` + `createViaAppComponentFactory` 原样搬出（2026-11-26）

## 职责

**只回答一个问题：怎么把 guest 的 Application 实例造出来。**

- 负责：按 AOSP `LoadedApk.makeApplicationInner()` 的降级顺序造实例 ——
  ① Android 9+ 先试 `AppComponentFactory.instantiateApplication`；② 反射无参构造；
  ③ 类找不到时回退到 base `android.app.Application`。
- **不负责**：`attach` / `attachBaseContext`（在 VAM 的 `attachBaseContext`）、
  `LoadedApk` 补丁、Provider 初始化、`onCreate` 调用、实例登记（`ApplicationRecord` 在 VAM）。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `classNameOf(applicationClassName: String?): String` | 空则回退 `android.app.Application` | 纯函数；**只有 `null` 才回退**，`""` 原样返回 |
| `create(packageName: String, className: String, classLoader: ClassLoader): Application?` | 造实例：工厂 → 反射 → 回退 | 失败返回 `null`（三种失败各自打日志，不抛） |
| `createViaAppComponentFactory(className, classLoader): Application?`（private） | 反射 `AppComponentFactory.instantiateApplication` | 内部 `runSafe` 吞异常；失败返回 `null` 表示"走反射"。**名字保持原样**（改名会削弱等价检查） |

**等价性结论（规则 10）**：`create` 与旧 `createApplication` 在忽略"被抽成 `classNameOf` 的那行赋值"后
**逐字等价（703 字符）**：

```powershell
git -C _ref/ShitJumpingVM show <上一提交>:<VAM 路径> > tools/tmp/vam-old.kt
node tools/lint/check-refactor-equivalence.mjs --old-file tools/tmp/vam-old.kt --old-fn createApplication `
  --new-file <GuestApplicationFactory.kt> --new-fn create --ignore "val className = applicationClassName"
```

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 新增 `classNameOf` | 原实现是函数内一行 `applicationClassName ?: "android.app.Application"`，现抽成公开纯函数以便单测 | **必要偏差（可测性）**，语义等价 | `GuestApplicationFactoryTest` G1–G3 |
| 参数 `packageName` 只用于日志 | `create` 的 `packageName` 不参与决策，仅复现原日志 `Creating Application: $className for $packageName` | 中性偏差（为日志一致性保留） | 日志 tag `VAppLifecycle` 不变 |
| 原 `createApplication` 的 `instanceId` / `virtualContext` / `appInfo` | 原实现里这三个参数**未被使用**；委托后仍保留在 VAM 签名上（调用方不变） | 中性偏差 | — |

## 约束与坑

- **不能纯 JVM 测 `create`**：需要真实 `android.app.Application` 与 `AppComponentFactory`（stub 会抛
  `RuntimeException: Stub!`）→ 只铆住 `classNameOf`；`create` 的三条路径由真机 breadcrumb
  （`instr newApplication <App>`，契约 C4）+ 日志覆盖。
- 日志 tag 必须与 VAM 一致（`VAppLifecycle`），否则按 tag 过滤的排查口径会断。
- `className == "android.app.Application"` 时直接取 `Application::class.java`，**不走**
  `classLoader.loadClass` —— 这一分支是为"guest 没有自定义 Application"准备的，改动它会改变
  默认 Application 的来源。
- 顺序不可交换：工厂在反射之前（AOSP 行为），工厂返回 `null` 才降级到反射。
