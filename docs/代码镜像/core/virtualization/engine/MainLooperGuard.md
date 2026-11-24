# 代码镜像：`core/virtualization/engine/MainLooperGuard.kt`

> 对应代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/engine/MainLooperGuard.kt`
> v150 从 `ActivityThreadHook.kt`（原 L147–221）拆出。

## 职责

**做什么**
- 在 guest 进程主 looper 外层装一层守卫，**只吞掉两类可证明是虚拟化导致的异常**：
  1. Toast 窗口的 UID mismatch（message 含 `Adding window failed` + cause 链里有 `SecurityException("... not in UID ...")`）
  2. GMS 的 `SecurityException("Unknown calling package name" / "does not belong to")`
- 其余异常一律 `throw`，保持 AOSP 的原生崩溃行为。
- 被吞的每一条都写 `GuestBreadcrumb.logThrowable`（可观测）。

**不负责**
- ❌ 不改消息内容、不做 Activity 拦截（`TransactionInterceptor` / `ActivityThreadHook` 负责）
- ❌ 不做崩溃上报/重试
- ❌ 不决定"哪些异常可以吞"的业务策略（目前写死在 message 白名单里，见偏差 #1）

## 函数

| 签名 | 作用 | 副作用 / 前提 |
| --- | --- | --- |
| `install()` | 在 `Handler(mainLooper).post {}` 里包 `while(true){ try{ Looper.loop() } catch{…} }` | **只允许调用一次**（由 `ActivityThreadHook.install()` 的 `installed` 保证）。失败仅记 warn，不影响启动 |
| `(private) 异常分类` | 扫整条 cause 链，判定"Toast UID mismatch" / "GMS SecurityException" | 不抛异常 |

## 偏差登记

| # | 偏差 | 相对谁 | 类型 | 理由 / 待办 |
| --- | --- | --- | --- | --- |
| **1** | **吞掉**两类 `SecurityException` | AOSP：主线程未捕获异常 → 进程崩溃 | ✅ **必要偏差** | ToastPresenter 用宿主 UID 校验虚拟包名，必然 mismatch；不吞则 guest 进程直接死。判断依据是 **message 字符串**，属脆弱耦合 → **OS 升级后失效时的默认行为是"照常崩溃"（安全侧）** |
| **2** | 在 `Handler.post` 里**再次调用 `Looper.loop()`**（嵌套 looper） | AOSP：`ActivityThread.main()` 只调一次 `Looper.loop()` | 🟡 **必要性的形式待优化** | 目标（捕获主线程异常）是必要的，但"嵌套 loop"让异常传播路径变长、崩溃栈与真机不一致 → **治本方向**：改用 `Thread.setDefaultUncaughtExceptionHandler`，只在其中做同样的分类判断 |
| **3** | 整条 cause 链扫描（不只 `root`） | 无（这是更正确的实现） | — | v115 修复：`RuntimeException("Adding window failed") -> SecurityException("not in UID") -> RemoteException` 的结构下，只看链尾会漏判 |

## 约束与坑

1. **必须保留 `throw e` 分支**：历史上出现过"无条件吞异常"的版本，表现为**白屏/点不动**而不是崩溃，排查成本极高。
2. **白名单失效要退化成崩溃**（当前已是），**不能**退化成"全部吞掉"。
3. **只装一次**：重复 `Looper.loop()` 嵌套会导致消息被处理多次。
4. 调试：被吞的异常必须能查到 —— `GuestBreadcrumb.logThrowable` + `Timber.w`；建议再加一条统计（吞了几次）到 `debug-trace.log`。
5. 本对象是 `internal object`，跨文件只能由 `core:virtualization` 内部调用。

## 相关

- 拆分来源与其它职责：`docs/代码镜像/core/virtualization/engine/ActivityThreadHook.md`
- 语义重构方案（含"改用 uncaughtExceptionHandler"）：`docs/计划/计划-ActivityThreadHook与Binder语义重构.md` §1.2
