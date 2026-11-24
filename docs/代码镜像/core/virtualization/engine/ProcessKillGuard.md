# 代码镜像：`core/virtualization/engine/ProcessKillGuard.kt`

> 对应代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/engine/ProcessKillGuard.kt`
> v150 从 `ActivityThreadHook.kt`（原 L1942–2020）整体迁出。

## 职责

**做什么**
- 阻止 guest 应用"自杀"式退出（`System.exit()`），避免连带杀掉整个容器 `:pN` 进程槽。
- 提供 `isActive()` 供其它 hook 查询"当前线程是否处于拦截态"。
- `uninstall()` 恢复原来的 `SecurityManager`。

**不负责**
- ❌ **拦不住 `Process.killProcess()`** —— 它经 JNI 直接 `kill(2)`，Java 层没有拦截点。
  完整方案需要 native hook（当前项目未做）。
- ❌ 不做进程恢复/重启（容器主进程与 `VirtualEngine` 负责）。
- ❌ 不处理"权限"（`checkPermission` 全量委托给原 manager）。

## 函数

| 签名 | 作用 | 副作用 / 前提 |
| --- | --- | --- |
| `install(): ProcessKillGuard` | 设置线程标志 + 安装 `NextVmSecurityManager` | **尽力而为**：`setSecurityManager` 失败只记日志，不阻断启动。幂等 |
| `isActive(): Boolean` | 当前线程是否在拦截态 | 只读 `ThreadLocal` |
| `uninstall()` | 清标志 + 还原 delegate | 必须与 `install()` 成对（由 `ActivityThreadHook.uninstall()` 调） |
| `KillInterceptedException` | 预留异常类型 | 当前未抛出 |

## 偏差登记

| # | 偏差 | 相对谁 | 类型 | 说明 |
| --- | --- | --- | --- | --- |
| **1** | 安装 `SecurityManager` 阻止 `System.exit()` | AOSP：应用可自由退出 | ✅ **必要偏差** | 容器里一个 guest 退出会带走整个 `:pN` 进程槽，影响同槽状态。不拦就无法运行 Unity 类应用 |
| **2** | 只拦 `System.exit`、不拦 `killProcess` | AOSP：两者都能退出 | ⚠️ **已知能力缺口** | 不是有意偏差，是 Java 层无拦截点；补法必须走 native（`kill` syscall / `Process.killProcess` 的 native 实现） |
| **3** | `ThreadLocal` 而非全局标志 | — | ✅ 正确实现 | 拦截语义是"本线程发起的退出"，避免误拦其它线程的正常退出 |

## 约束与坑

1. **`System.setSecurityManager` 在新 Android 上可能直接抛异常或被忽略** → 本守卫是"尽力而为"，
   **不能作为可靠性保证**。若要真正可靠，必须补 native 层的 `kill`/`exit` 拦截。
2. `checkPermission` **必须全量委托**给原 manager，否则会误伤 guest 的正常权限判断。
3. `install()` 幂等：重复调用只更新标志，不会重复包装 `SecurityManager`（已有 `is` 判断）。
4. `uninstall()` 只还原"我们自己装的"那个（`current is NextVmSecurityManager`），不会破坏别人装的。
5. 调试：拦截发生时打 `Timber.i("Blocked System.exit(...)")`；排查"应用为什么退出不了"时先看这里。

## 相关

- `docs/代码镜像/core/virtualization/engine/ActivityThreadHook.md`（拆分来源与其它职责）
- `docs/架构/架构-文件拆分契约模式.md`（拆分原则）
