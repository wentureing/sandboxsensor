# 代码镜像：`core/virtualization/lifecycle/GuestPathSetup.kt`

> 对应代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/GuestPathSetup.kt`
> v152 从 `VirtualApplicationManager.kt`（原 L1208–1320）迁出。

## 职责

**做什么**（两个 `internal` 扩展函数，作用于 `VirtualApplicationManager`）
- `registerGuestRedirections(app, instanceId, guestPkg)`（v142）
  在 **guest 进程**注册本实例的路径重定向规则：`/sdcard/…`、`/data/user/0/<guest>/…` → 沙箱。
- `patchFrameworkDataDir(app, guestPkg)`（v143）
  把 `LoadedApk.mApplicationInfo.dataDir` / `mDataDir` 注入为 guest 官方路径（仅白名单）。

**不负责**
- ❌ ActivityThread 字段改写（v138，仍在 `VirtualApplicationManager`）
- ❌ 目录树实际创建（`SandboxManager.ensureAppDirsReady`）
- ❌ native 侧 GOT patch / `redirect_path` 实现（`NativeHookBridge` / `native-hook.cpp`）
- ❌ 决定 `/sdcard` 重定向的**内容**（规则内容由 `NativeHookBridge.setupAppRedirections` 定义）

## 函数

| 签名 | 作用 | 副作用 / 前提 |
| --- | --- | --- |
| `VirtualApplicationManager.registerGuestRedirections(app, instanceId, guestPkg)` | 注册 11 条左右的重定向规则到**本进程**的 native 表 | **前提**：native 库已加载；`application.filesDir.parentFile` 已指向沙箱。失败只记 `fail`，不抛 |
| `VirtualApplicationManager.patchFrameworkDataDir(app, guestPkg)` | 反射把 `LoadedApk` 的 dataDir/mDataDir 改成 `/data/user/0/<pkg>` | 仅 `com.sandbox.probe` 生效；开关 `debug.nextvm.webviewDataDirFake` |

## 偏差登记

| # | 偏差 | 相对谁 | 类型 | 说明 |
| --- | --- | --- | --- | --- |
| **1** | 在 guest 进程**重复注册**路径规则（主进程 `launch()` 也注册过） | 无（容器设计如此） | ✅ **必要偏差** | `NativeHookBridge` 的规则表是**进程级**（@Singleton，每进程一份）。只在主进程注册会让 guest `rules=0` —— 这是实测过的故障（v142 修复） |
| **2** | 把 `dataDir` 报成 guest 官方路径 | AOSP：dataDir 即真实数据目录 | 🟡 **价值待重新确认** | 原假设："让 WebView 认为文件不在自己 dataDir 内 → 走标准 MIME 判定"。**已被否证**（v146：W37 显示 dataDir 已变，W2 仍是 `text/plain`）。目前仅白名单 guest 启用，保留供实验 |
| **3** | 只对 `com.sandbox.probe` 启用假 dataDir | — | ✅ 保守选择 | 避免影响真实应用；白名单在 `VirtualContext.detachedAppInfoEnabled()` 里写死 |

## 约束与坑

1. **必须在 native 库加载之后调用**（规则要写进 native 的 `g_path_redirects`）：
   即 `bindApplication` 中 `initNativeHooks` 之后。顺序错了规则只在 Kotlin 侧、native 侧为空。
2. **规则是进程级**：`LAUNCH` 复用进程时不会重新注册；进程重建才会。
3. **失败不抛**：全部 try/catch + `DebugTrace.fail`，因为它在 bind 主流程上。
4. `appDir` 取自 `application.filesDir.parentFile` —— 依赖 `VirtualContext.getFilesDir()` 已指向沙箱；
   在 `pathFidelity` 模式下会指向合成路径，需注意。
5. 两个函数是**扩展函数**，依赖 `VirtualApplicationManager` 把
   `nativeHookBridge` / `sandboxManager` / `fieldOf` 暴露为 `internal`（v152 改；只改修饰符不改名）。
6. debug：命中/跳过/失败都写 `debug-trace.log`（tag = `v142` / `v143`）。

## 可测性

| 可测部分 | 方式 |
| --- | --- |
| 开关判断（`guestRedirect` / `webviewDataDirFake`） | 纯 JVM：`DebugFlags` 可注入（`SystemProperties` 反射失败时走默认值） |
| 规则内容（11 条） | 需要 native 库 → 目前靠真机探针验证（`debug-trace.log` 的 `v142 registered rules=N`） |
| 白名单判断 | 纯逻辑，可抽成 `isFakeDataDirWhitelisted(pkg)` 后单测（**待做**） |

## 相关

- `docs/代码镜像/core/hook/JavaIoHooks.md`（Java 层重定向，默认关闭）
- `docs/记录/记录-假dataDir假设被否证.md`
- `docs/架构/架构-注入时机审计.md`（为什么"注册点≠生效进程"是个坑）

## 可测性（已落实）

| 可测部分 | 方式 | 用例 |
| --- | --- | --- |
| 白名单判断 \isFakeDataDirWhitelisted(packageName)\ | 纯函数，已抽出 | \GuestPathSetupTest\ 3 例（只有探针在内 / 真实应用不在 / 近似包名不匹配） |
| 开关判断（\guestRedirect\ / \webviewDataDirFake\） | 由 \DebugFlags\ 覆盖 | — |
| 规则注册、native 同步 | ❌ 需要 native 库 | 靠真机 \debug-trace.log\ 的 \142 registered rules=N\ 验证 |

**已抽出的纯函数**：\isFakeDataDirWhitelisted(packageName: String): Boolean\（v158）。
