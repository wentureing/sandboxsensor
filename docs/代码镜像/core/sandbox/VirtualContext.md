# VirtualContext（core/sandbox）

- 代码：`core/sandbox/src/main/java/com/nextvm/core/sandbox/VirtualContext.kt`
- 测试：`core/sandbox/src/test/java/com/nextvm/core/sandbox/PathWhitelistEffectTest.kt`（W1–W7）
- 日期：2026-11-25

## 职责

`ContextWrapper` 子类：决定 guest **「看到什么路径」**（L1 层），把每个 `get*Dir()` 指向沙箱，
并按白名单/豁免决定对外暴露「真沙箱路径」还是「合成路径」。

**不负责**：

| 不负责 | 在哪 |
| --- | --- |
| I/O 翻译（用起来去哪） | L3 `RegisterNatives` / L5 native GOT（见 `架构-hook实现现状-代码为准.md`） |
| 规则注册 | `NativeHookBridge.setupAppRedirections` 等 |
| 是否启用合成（策略） | `PathPolicy` / `VirtualConstants.isPathFidelityGuest` |
| 合成路径模板 | `PathKinds` |

## 函数

| 函数 | 作用 | 关键前提 / 副作用 |
| --- | --- | --- |
| `contentDir(sandboxDir, synthetic)` | 内容目录的唯一决策点 | 判据是包级 `shouldUseSyntheticContent`；**不建目录** |
| `shouldUseSyntheticContent(pathFidelity, contentPathsUseReal)` | 包级 `internal` 纯函数：`pathFidelity && !contentPathsUseReal` | 抽出来是为了**纯 JVM 可测**（`VirtualContext` 需要 Android 运行时才能构造） |
| `getFilesDir` / `getCacheDir` / `getCodeCacheDir` / `getNoBackupFilesDir` / `getDatabasePath` / `getDir` / `getExternalFilesDir` / `getExternalCacheDir` / `getObbDir` / `getExternalMediaDirs` | 走 `contentDir`（先 `sandboxManager.ensureDir`，再决定对外哪条路径） | **有建目录副作用** |
| `getDataDir` | 不受 `contentPathsUseReal` 影响：`pathFidelity || detachedAppInfoEnabled()` 才合成 | 反双开检测最常看这里 |
| `getApplicationInfo` | 返回副本（v140）并按 `detached` 决定 `dataDir` | `detachedAppInfoEnabled()` 只对 `com.sandbox.probe` 生效 |
| `getOpPackageName` | 返回 `HOST_PACKAGE` | 必要偏差：避免 AppOps 校验抛错；代价是 guest 会拼出宿主包名路径 |

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | **`contentPathsUseReal = false`（全局强制真）** | WebView 渲染在 isolated 进程（无 hook），假路径读不到 → 主页空白（`架构-WebView假路径换真路径.md`） | 必要偏差（过渡） | `Dbg/ctx: getFilesDir -> ...` 含 `/files/virtual/data/` |
| 2 | 内容目录与 `getDataDir` 走**不同**判据 | 文件必须真的能读写（真路径）；反双开只看 `getDataDir`（假路径） | 必要偏差 | 两者日志对照 |
| 3 | `getOpPackageName` 返回宿主包名 | AppOps 校验需要宿主身份 | 必要偏差 | guest 拼出 `/storage/emulated/0/Android/data/com.nextvm.app/...`，由 `hostFallbackRules` 兜底 |
| 4 | `detachedAppInfoEnabled` 只对探针包生效 | 受控实验（W36/W37）需要假 dataDir | 中性偏差 | `Dbg/appInfo detached=` |
| 5 | `pathFidelity` / `contentPathsUseReal` 都是 **构造时求值** 的 `val` | 避免每次 `get*Dir()` 都查策略 | 中性偏差 → 改配置**必须重启** | `SPOOF_EXEMPT_*` 命令输出"重启后生效" |
| 6 | `contentPathsUseReal` 与 `PathPolicy` 语义重叠 | 前者全局硬编码，后者 per-guest 可配置；B 阶段应收敛为查 `PathPolicy` | **待修偏差** | W6 已把判据隔离成一处，接线时只改 `shouldUseSyntheticContent` |

## 约束与坑

1. **不要绕过 `contentDir` 直接返回 `File(synthetic...)`**：那会让"白名单/豁免是否生效"无法用
   `shouldUseSyntheticContent` 一处验证（当前 13 处分支里已有少数直接构造，见 `架构-路径转换分层现状`）。
2. **`ensureDir` 在决策之前**：即使最终返回合成路径，沙箱目录也已被创建 —— 这是"写入能落沙箱"的前提，
   不要为了"少建目录"把它挪到分支里。
3. **`contentPathsUseReal` 是构造期常量**：改它只影响新构造的实例（= 重启 guest 后）。
4. **日志**：本文件用 `DebugTrace.event/detail`（release 不产出）。新增日志时必须保持
   `DebugFlags.isDebugBuild()` 门禁，且不得让日志写入依赖被 hook 的层。
5. **白名单生效的自动化验证**（`PathWhitelistEffectTest`）：
   `path_spoof.conf` 两份名单 → `isPathFidelityGuest` → `shouldUseSyntheticContent`；
   隔离侧由 `PathPolicy.isRedirectionEnabled` + `PathKinds.rulesFor` 断言规则始终注册。
   **不要把 `contentDir` 改成 private 内联**，否则这条测试会退化成"测一个布尔表达式"。
