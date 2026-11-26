# PackerAttachFixes

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/startup/PackerAttachFixes.kt`
> 来源：2026-11-27 B4-4f 从 `VirtualApplicationManager.patchJiaguStubBeforeAttach` + `installRuntimeLoadCallerFix` 搬出

## 职责

**只回答一个问题：打包壳（Jiagu / SecNeo / 360）在 `attach` 前需要哪些运行时修正。**

- 负责：
  1. `applyBeforeAttach`：stub Application 的 String 字段（native lib 路径 / data 路径）为空或指错时改对；
  2. `applyRuntimeLoadCallerFix`：把线程 `contextClassLoader` 指到 guest CL，并给 guest Application 类补
     `ProtectionDomain`（CodeSource = guest APK）。
- **不负责**：判断"要不要走这两段"（由 `VAM.attachBaseContext` 决定）、路径注入（`GuestLoadedApkPathInjection`）、
  native 侧的 `hookRuntimeNativeLoad`（`NativeHookBridge`）。

## 函数

| 签名 | 作用 | 关键前提 / 副作用 |
| --- | --- | --- |
| `isPackerStubClass(className): Boolean` | 类名含 `stub/jiagu/wrapper/Shell/Qihoo`（ignoreCase）→ 判为壳 | 纯函数 |
| `redirectedPath(value, dataDir): String?` | 含 `/data/data/` 且不含容器包名才替换；**null = 不改** | 纯函数（原判据 `redirected != value`） |
| `applyBeforeAttach(application, virtualContext)` | 遍历 `declaredFields`，空 String 填 nativeLibraryDir、错误路径改 dataDir | 非壳类直接 return；失败只打 W |
| `applyRuntimeLoadCallerFix(application, virtualContext)` | 设线程 CL + 补 ProtectionDomain | 失败只打 W；`pd` 已有 codeSource 时只打日志 |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| `isPackerStubClass` / `redirectedPath` 从内联抽出 | 原为函数内 5 个 `contains` 与一段 `replace` | **必要偏差**（可测性），语义逐条对应 | `PackerAttachFixesTest` P1–P9；`check-refactor-equivalence` 报"不等价"= 预期，已在本表登记 |
| 日志字符串保持原样 | `Detected packer stub class:` / `Patched null field:` / `Redirected field:` / `Injected ProtectionDomain` / `non-fatal` | 无偏差 | 逐字相同（`applyRuntimeLoadCallerFix` 在等价检查里 ✓） |

## 约束与坑

- **只在 legacy attach 链上执行**：framework 路径由 `LoadedApk.makeApplication()` 内部 attach，不经过这里（L6 的观测点）。
- `sourceDir` 在 `applyBeforeAttach` 里只做"非空校验"，不参与写字段 —— 但去掉它会改变 `?: return` 的短路语义，**不要删**。
- `redirectedPath` 只认 `/data/data/`（原实现如此）：`/data/user/0/...` 的值原样不动（`P7` 固定这个行为）；若以后要支持，属行为变更。
- 壳字段修正会**静默跳过不可访问字段**（`catch` 空块）：排查"某壳还是崩"时，先确认字段名与可访问性，不要只看日志。
- 新增壳特征（如 `SecShell`）时只改 `isPackerStubClass` 并补 `P` 系列用例；不要在 `applyBeforeAttach` 里再写一份判据。
