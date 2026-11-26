# 计划：接管后下一步拆分与测试计划

- 日期：2026-11-26（接管轮）
- 状态：**⏸ 暂停** —— 2026-11-27 用户改主线：先修 process slot 会话复用问题
  （`docs/输出/变更/20261127-process-slots-会话复用与UI解绑.md`），B4-4 重构暂停；本文件的 L1 副作用研究（§3.2）
  仍然有效并被新计划引用（新计划走"会话复用"而非"归还兜底"路线）
- 依据：`AGENTS.md`（规则 7/9/10/12/13/18）、`STATUS.md`、`docs/输出/记录/记录-本轮流程复盘与改进.md`、
  `docs/计划/计划-B4-4剩余批次边界.md`、`docs/计划/计划-启动链已知缺陷（重构后修复）.md`
- 代码基线：容器仓库 `_ref/ShitJumpingVM` 当前 `0798d4b`（B4-4 a–e 已完成）

---

## 一、交接核对：现状与文档的 4 处偏差（执行前必须先认这些）

| # | 项 | 文档说的 | 实测（`0798d4b`） | 影响 |
| --- | --- | --- | --- | --- |
| 1 | VAM 体积 | 1 795 行 | **1 896 总行 / 1 795 非空行**（类体 `:68-1381` 1314 行 + 顶层函数 `:1383-1896` 514 行） | 只是口径差（STATUS 用的是非空行），无实质问题 |
| 2 | `loadedApkWitness` + `secNeoLoadProbe` 的量 | 「63 行」 | **513 行**：`loadedApkWitness` `:1383-1409`（27）+ `secNeoLoadProbe` `:1411-1896`（**486 行单函数**） | **B4-4g 的体量估计错了 8 倍**，原计划"g 一批 232 行"不成立，必须重排（见 §五） |
| 3 | 缺陷文档的行号 | L6：`VAM:1427` / `:1304` / `:1373` | 现为 `:1046` / `:923` / `:992`（L3 引的 `:1140` / `:265` 也已漂移） | 引用缺陷文档前要重新定位；修 L3/L6 时不能照旧行号找 |
| 4 | L1 是否已修 | 未决 | `StubRegistry.releaseAllStubsForSlot` `:97` **仍然零调用点**（`VirtualEngine.stopApp` `:726-759` 只 `processManager.releaseSlot`） | 确认 L1 未修，且落点已定位（见 §三） |
| 5 | **新发现 L11** | 无（此前未登记） | 容器 LAUNCH 顺序分配 `Standard00…04`，而 **guest 内部 `startActivity` 硬编码 `Standard00`**（`ActivityManagerProxy.kt:1134-1137`）→ 修好 L1 后每次 `STOP→LAUNCH` 都回到 `00`，与 guest 内部用名**持续重叠**，`VirtualActivityManagerService.activityStack` 按 stub 名 `put` 覆盖（`:204`） | 新登记 **L11**（§3.4），**不夹带在本批修**；需你决定是否紧随 L1 之后单独立项 |

**另一条口径提醒**（本轮实测校核过，写进计划以免误判）：`VAM` 的 `loadedApkWitness` / `secNeoLoadProbe` 是**文件级顶层私有函数**
（`:1381` 处类的 `}` 已闭合），因此它们**不访问任何 VAM 成员/状态** —— 这使 B4-4g 的取证部分可以做**零注入逐字搬迁**，
比原文档预估的"需 `--ignore` + 偏差登记"更干净。

---

## 二、批次排序（推荐顺序 + 需要你拍板的 2 个点）

| 序 | 批次 | 职责 | 体积 | 为什么排这里 |
| --- | --- | --- | --- | --- |
| **P0** | **B4-5a** L1 修复 | `stopApp` 释放 stub（+ 单测） | ~6 行 + 1 测试 | **每批真机回归要 8 次启动（2 guest × 2 次 × 2 路径），而 L1 上限是每 slot 5 次** —— 不先修，后续每批回归都会被强制"重启容器"打断（复盘文档 §四 已列为第一优先） |
| **P1** | **B4-4f** | ① guest 路径注入宿主 `LoadedApk` ② 打包壳 attach 前修正（Jiagu 字段 + ProtectionDomain） | 51 + 95 = 146 行，2 新文件 | 零注入、零 VAM 状态 → 可 100% 逐字等价；且是 **L6 的三分之二落点** |
| **P2** | **B4-4f′** | ① `attachBaseContext` 三级 attach 编排 ② `disableCheckJniForGuest` | 66 + 21 = 87 行，2 新文件 | 依赖 P1 已搬好的三个 object → **不需要函数类型参数/回调注入**（复盘文档禁用的写法直接避开） |
| **P3** | **B4-4g** | ① framework bind 前取证（`loadedApkWitness` + `secNeoLoadProbe`）② stock Instrumentation 换装/还原 + 其状态 | 513 + 44 = 557 行，2 新文件 | 顶层函数，零注入；但 486 行单函数在批内登记为待拆 |
| **P4** | **B4-4g′** | `bindViaFrameworkLoadedApk` 编排 | 161 行，1 新文件 | 唯一需要注入 `resourceManager` + 记录表（用 interface，不用 lambda 参数） |
| **P5** | **B4-4h** | `bindApplication` **收敛**（不搬出） | 204 → ~130 行 | 见 §六：**改变原计划**（原计划是"搬出编排"，会让 VAM 只剩空壳，违背规则 7「职责单一即可」） |
| **P6** | B4-5b L2 修复 | `launch` 时认领持久化 slot | ~15 行 + 单测 | L2 与 L1 叠加会让真机回归更早失败；但 L2 只在"容器重启后直接 LAUNCH 已安装实例"时出现，优先级低于 L1 |

### 需要你拍板的 2 个点

| # | 问题 | 我的建议 | 反方案 |
| --- | --- | --- | --- |
| **D1** | **先修 L1（P0）再拆分，还是先拆 f（P1）？** | **先修 L1**（已获准）。证据：本轮 8 份 golden 采集 = 8 次启动 > 5 次/进程；不修则每批回归都会撞 `No available stub`，被迫重启容器（复盘文档 #11）。**副作用研究见 §三**（已把"与 guest 内部 `Standard00` 重叠"新登记为 L11，见 §三.4） | 先拆 f：好处是不打断"拆分节奏"，代价是每批真机回归要分段做、每段前重启容器 |
| **D2** | **B4-4h 是"搬出编排"还是"收敛编排"？** | **收敛**（`bindApplication` 留在 VAM），**详细现状/功能/理由见 §六** | 照原计划搬出 204 行到 `startup/BindOrchestrator.kt`（成本与收益对比见 §六.4） |

---

## 三、P0 / B4-5a：L1 修复（`stopApp` 释放 stub）

### 目标

`STOP` 之后同一 `processSlot` 可以无限次重新 `LAUNCH`，不再出现 `No available stub for process slot N`。

### 影响范围

| 文件 | 类型 | 改动 |
| --- | --- | --- |
| `core/virtualization/.../engine/VirtualEngine.kt` | 修改 | `stopApp`（`:726-759`）：在 **`SIGKILL` 之后**（`:754-757` 之后）释放该 slot 的 stub，带 `::stubRegistry.isInitialized` + `runningApps.values.none { it == slot }` 双重守卫 |
| `core/virtualization/src/test/.../engine/StubRegistryTest.kt` | 修改 | 新增 3 个用例（见 §七 R1–R3） |
| `docs/输出/变更/20261127-l1-stub-release.md` | 新增 | 变更文档（这是**行为变更**，按规则 18.1 走变更流程） |
| `docs/计划/计划-启动链已知缺陷（重构后修复）.md` | 修改 | L1 标记为已修 + 附 commit；顺带修正 §一 里过时的行号（偏差 #3） |
| `docs/现状/README.md` / `STATUS.md` | 修改 | 能力状态表 + 未决问题 #2 同步（规则 6） |

### 3.1 逐文件 diff 预览（**已按副作用研究修订**）

```diff
 // VirtualEngine.kt :726  stopApp
     fun stopApp(instanceId: String): VmResult<Unit> {
         val slot = runningApps.remove(instanceId)
         if (slot != null) {
             // Kill process in VAMS (takes instanceId, not slot)
             serviceManager.activityManager.killProcess(instanceId)
             processManager.releaseSlot(slot)
-            // …
             classLoaders.remove(instanceId)
             // …（storage hook / GMS cleanup / updateAppState 不变）
         }
         // VM 的 killProcess 只改记录，真实进程仍在 —— 必须真的杀
         val pkg = findApp(instanceId)?.packageName ?: instanceId.substringBefore('_')
         val kill = runCatching { GuestProcessStopper.stop(pkg) }
             .getOrElse { GuestProcessStopper.StopResult(emptyList(), 0) }
         Timber.tag(TAG).i("Stopped app: $instanceId (pids=${kill.pids} killed=${kill.killed})")
+        // L1：stub 池按 slot 释放，否则每 slot 只够 5 次启动。
+        // 位置必须在 SIGKILL 之后（记录已清、进程已死）；
+        // 守卫 1：同 slot 还有别的实例在跑就不释放（L2 未修时两实例共用 slot）；
+        // 守卫 2：容器重启后 stubRegistry 可能尚未初始化。
+        if (slot != null && ::stubRegistry.isInitialized &&
+            runningApps.values.none { it == slot }
+        ) {
+            stubRegistry.releaseAllStubsForSlot(slot)
+            Timber.tag(TAG).i("Released stubs for slot $slot (killed=${kill.killed}/${kill.pids.size})")
+        }
         return VmResult.Success(Unit)
     }
```

三点修订理由（相对首版计划，**其中第 1 条是首版计划的真实错误**）：

| 修订 | 为什么 |
| --- | --- |
| **① 释放点从 `releaseSlot` 之后挪到 `SIGKILL` 之后** | 首版把释放写在 `if (slot != null)` 块内（`:731` 附近），那里 `kill`（`:755`）**还没定义** → 既编译不过，语义上也变成"杀进程前就归还 stub"。正确顺序：`killProcess`（清 VAMS 记录）→ `releaseSlot` → `SIGKILL` 真实进程 → 归还 stub |
| **② 加 `runningApps.values.none { it == slot }` 守卫** | §三.2 的副作用 #2：L2 未修时 `mark.via` 与 `com.sandbox.probe` 同 `slot=1`（`LIST` 实测），无守卫的释放会把**另一个实例正在用的 stub 名**还回池子 |
| **③ 释放日志带 `killed/pids.size`** | 副作用 #1 的可观测性：若没杀干净（`killed < pids.size` 或 `pids` 为空），日志立刻看出来；对应镜像文档「偏差登记」四问里的"可观测" |

### 3.2 全局副作用研究（8 项，逐项给证据）

| # | 副作用 | 事实（文件:行） | 判断 / 处置 |
| --- | --- | --- | --- |
| **1** | 旧进程若没被真正杀死，同名 stub 会被两个 Activity 共用 | `GuestProcessStopper.stop` 用 `SIGKILL`（`GuestProcessStopper.kt:63-74`），`stopApp` 在 `:754-757` 调用；`StopResult.ok` **当前没人检查** | **不引入新失败模式**（现状第 6 次根本起不来）。处置：释放日志带 `killed/pids`（§3.1）；不因"杀不干净"而拒绝释放（那会变成死锁） |
| **2** | **与 L2 叠加**：释放会把另一实例的 stub 还回池子 | `processSlot` 是安装时持久化值（`VirtualEngine.kt:490` + `VirtualApp.processSlot`）；容器重启后 `VirtualProcessManager.slots` 全空（`VirtualProcessManager.kt:24-29`），`launch` 直接用持久化值（`:632`）；`LIST` 实测两实例同为 `slot=1` | **真实副作用，必须加守卫**（§3.1）。L2 修好后守卫恒真、等价于无条件释放，可保留 |
| **3** | 复用 stub 名与 VAMS 记录错乱 | `activityStack` 以 **stubClassName 为 key**（`VirtualActivityManagerService.kt:204`），`killProcess` 清该实例全部记录（`:160-161`） | **无新增风险**：`killProcess` 在释放之前执行，旧记录已清。前提是走 `stopApp`（`CmdReceiver.kt:142`）+ `uninstallApp`（`:130` → `stopApp` `:768`） |
| **4** | ⚠️ **`Standard00` 与 guest 内部固定 stub 重叠 —— 修复后从"第 1 次"变成"每次都重叠"** | 容器 LAUNCH 顺序分配 `Standard00…04`（`VirtualEngine.kt:631` + `StubRegistry.kt:56`，常量 5 个 `ProcessSlot.kt:107`）；而 guest **内部** `startActivity` 的 stub 名**硬编码** `P<slot>$Standard00`（`ActivityManagerProxy.kt:1134-1137`，`alternateStandardStub` 再按 `%5` 轮换 `:414-419`） | **必须让用户知情**：修复后每次 `STOP→LAUNCH` 都回到 `Standard00`，与 guest 内部固定用名持续重叠；`activityStack[stub]` 是 `put` 覆盖（`:204`）→ 记录层可能丢一条旧记录。**本批不改**（改 `getStubForApp` 起始号属行为变更），登记为 **新缺陷 L11**（§3.4） |
| **5** | service / provider / receiver stub 会不会被误释放 | `releaseAllStubsForSlot` 释放该 slot **全部组件**（`StubRegistry.kt:97-101`）；但生产代码**只调用 `resolveStub`**（全仓仅 `VirtualEngine.kt:631`），`resolveServiceStub` / `resolveProviderStub` / `isStubComponent` / `getSlotForStub` **零调用点**（`docs/现状/零调用扫描报告.md:221-231` 已登记）；`getServiceStubForApp` 是硬编码 `$S00`（`ActivityManagerProxy.kt:1139-1142`），**不经过 StubRegistry** | **无副作用**：这些名字本来就从未进入 `inUseStubs`，`remove` 对不存在的键是 no-op |
| **6** | 是否影响 PMS/AMS 的组件注册 | stub 组件在 `AndroidManifest.xml` 里静态声明，`StubRegistry` 只是内存集合（`StubRegistry.kt:26-29`） | **无系统侧副作用**：不涉及 PMS/AMS 注册与卸载 |
| **7** | 线程安全 | `inUseStubs` 是 `mutableSetOf`（非并发，`StubRegistry.kt:29`）；`resolveStub`（`launch`，主线程）/ 新加的 `releaseAllStubsForSlot`（`stopApp`，主线程） | **现状即如此**，本批不改变并发模型；登记进镜像文档「约束与坑」 |
| **8** | 文档 / 脚本 / 测试连带 | ① `docs/架构/架构-启动链契约.md` §六 边界表 #1 写着"根治见 N1"；② `docs/现状/零调用扫描报告.md:228` 把 `releaseAllStubsForSlot` 列为零调用；③ `STATUS.md` §2 #2；④ `tools/device/capture-breadcrumb-golden.ps1:124,174` 两处提示"若报失败先重启容器" | **同批更新** ①②③（规则 6）；④ 只改文案（低优先，可选） |

**结论**：8 项里真正改变实现的只有 3 处 —— #1 修正释放位置（首版计划的真实错误）、#2 加同 slot 守卫、#4 登记 L11；
其余 5 项是"确认无影响"或"文档同步"。

### 3.3 回退与验证补充

真机验证三条（都要做）：

1. **「STOP→LAUNCH ×6」**：第 6 次仍 `bind complete`（L1 的直接验收口径）；
2. **「守卫生效」**：L2 未修时 via 与 probe 同为 `slot=1` →
   `LAUNCH via` → `LAUNCH probe` → `STOP via`，此时 `stopApp` 的释放日志**不应出现**
   （`runningApps` 里 probe 仍占 slot 1）；随后 `STOP probe` 才应出现 `Released stubs for slot 1`；
3. **「交替实例不互踩」**：`STOP via` 后 `LAUNCH probe` 必须正常启动，且两者的 stub 名不同（via 用 `Standard00`、probe 用 `Standard01`，见 `activityStack` 记录 / `Started activity: … → …P1$Standard0x`）。

### 3.4 本批新登记的缺陷 L11（**不在本批修**）

| 项 | 内容 |
| --- | --- |
| 现象 | 容器 LAUNCH 分配到的 stub 名与 guest 内部 `startActivity` 固定使用的 `P<slot>$Standard00` 重叠（`ActivityManagerProxy.kt:1136`）；L1 修好后每次 `STOP→LAUNCH` 都回到 `Standard00` |
| 后果 | `activityStack[stub]` 被 `put` 覆盖（`:204`）→ 同一 stub 名上新旧两条 `VirtualActivityRecord` 只剩一条；`finishActivity`/`updateActivityState` 可能作用到错误记录 |
| 为什么不是本批的问题 | 现状第一次 LAUNCH 就已重叠；本批只是让它**每次**都发生（因为池子会被归还） |
| 候选修法（二选一，另立变更） | ① `getStubForApp` 改用 `Standard04`（容器顺序分配要到第 5 次才用到，冲突概率趋零，`%5` 轮换仍合法）；② `resolveStub` 跳过"该 slot 上仍有活跃 activity 记录"的 stub（按 `activityStack` 反查） |
| 验证 | via 内部跳转（主页→下载页→返回）后 `activityStack` 每条 stub 记录唯一；`files/` 无异常；探针 J 系列不回退 |

### 回退

```powershell
git -C _ref/ShitJumpingVM revert <B4-5a commit>
```

### 验证

```powershell
node tools/lint/kotlin-lint.mjs _ref/ShitJumpingVM/core/virtualization/src
node tools/lint/check-imports.mjs _ref/ShitJumpingVM/core/virtualization/src
node tools/lint/check-test-fn-names.mjs _ref/ShitJumpingVM/core/virtualization/src/test/java/com/nextvm/core/virtualization/engine/StubRegistryTest.kt
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:virtualization" test_filter="com.nextvm.core.virtualization.engine.*"
```

真机判据（**判定只读文件，不看 logcat**）：

1. `STOP` + `LAUNCH` **连续 6 次** same guest，第 6 次仍 `bind complete`（breadcrumb），**不出现** `No available stub`；
2. `files/checks.log` 仍 `hookcheck ok=8 fail=0`。

---

## 四、P1 / B4-4f：guest 路径注入 + 打包壳 attach 前修正

### 4.1 两个职责与去向

| # | 职责 | 现状（`VAM` @ `0798d4b`） | 去向（新文件） | 依赖 |
| --- | --- | --- | --- | --- |
| ① | **把 guest 的路径写进宿主 `LoadedApk`**（`mApplicationInfo` 的 4 个路径字段 + `mLibDir`） | `injectGuestPathsIntoLoadedApk` `:1046-1096`（51 行） | `lifecycle/startup/GuestLoadedApkPathInjection.kt` | 只用 `virtualContext` + `findField` + `Timber` → **零 VAM 状态** |
| ② | **打包壳（Jiagu/SecNeo）在 attach 前修正**：stub 类里的空/错误路径字段、`Class.protectionDomain` | `patchJiaguStubBeforeAttach` `:923-975`（53）+ `installRuntimeLoadCallerFix` `:992-1033`（42） | `lifecycle/startup/PackerAttachFixes.kt` | 只用 `application` + `virtualContext` + `findField` → **零 VAM 状态** |

**为什么不把 `attachBaseContext` 一起放这批**（原文档 f 是这么排的）：`attachBaseContext` 要依次调用
①、②、`disableCheckJniForGuest`（还在 VAM）。若同批搬，就得给新函数**注入回调**；而复盘文档 #9/#10 已证明
"函数类型参数"会破坏 `check-refactor-equivalence` 的花括号配对。**先把被调用方（叶子）搬成 object，下一批
`attachBaseContext` 直接 import 静态调用即可，注入数 = 0。**

### 4.2 新文件形态

```kotlin
// lifecycle/startup/GuestLoadedApkPathInjection.kt
object GuestLoadedApkPathInjection {
    /** 注入的 ApplicationInfo 路径字段（顺序即原代码顺序，防止漏字段）。 */
    internal val APP_INFO_FIELDS = listOf("nativeLibraryDir", "sourceDir", "publicSourceDir", "dataDir")
    fun apply(virtualContext: Context)   // 原 :1046-1096 函数体，日志逐字不变
}
```

```kotlin
// lifecycle/startup/PackerAttachFixes.kt
object PackerAttachFixes {
    /** 壳类名判据（原为内联 5 个 contains，ignoreCase）。 */
    internal fun isPackerStubClass(className: String): Boolean
    /** 字段值重定向：返回 null 表示不改（原为 `redirected != value` 判据）。 */
    internal fun redirectedPath(value: String, dataDir: String): String?
    fun applyBeforeAttach(application: Application, virtualContext: Context)  // 原 :923-975
    fun applyRuntimeLoadCallerFix(application: Application, virtualContext: Context) // 原 :992-1033
}
```

搬运要点（**逐字**）：

- 日志字符串一字不改：`Detected packer stub class: …` / `  Patched null field: … → …` / `  Redirected field: …` /
  `Set thread contextClassLoader to guest ClassLoader: …` / `Injected ProtectionDomain with CodeSource: …` /
  `Injected mLibDir into LoadedApk: …` / `Injected guest paths into LoadedApk: nativeLibDir=…`；
- `Timber.tag(TAG)` 的 `TAG` 仍是 `"VAppLifecycle"`（两个新文件各声明 `private const val TAG = "VAppLifecycle"`，
  **不改 tag** —— golden 与 logcat 切片依赖它）；
- 每个 `catch` 的"non-fatal"语义保持（never throw）。

### 4.3 等价检查（规则 10）

```powershell
git -C _ref/ShitJumpingVM show 0798d4b:core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/VirtualApplicationManager.kt `
  > tools/tmp/vam-0798d4b.kt

node tools/lint/check-refactor-equivalence.mjs `
  --old-file tools/tmp/vam-0798d4b.kt --old-fn injectGuestPathsIntoLoadedApk `
  --new-file _ref/ShitJumpingVM/core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/startup/GuestLoadedApkPathInjection.kt `
  --new-fn apply
```

② 用 `--old-fn patchJiaguStubBeforeAttach --new-fn applyBeforeAttach`、`--old-fn installRuntimeLoadCallerFix --new-fn applyRuntimeLoadCallerFix` 各跑一次。

| 预期 | 说明 |
| --- | --- |
| ① 等价 ✓ | 纯搬迁 |
| ② **不等价**（若判据被抽成 `isPackerStubClass` / `redirectedPath`） | 结构性改造（内联判据 → 纯函数），**允许**，但必须在镜像文档「偏差登记」写：`contains` 五连判据与正则替换被抽为纯函数、语义逐字对应、由 P1–P9 单测固定 |

> 若希望 ② 也判"等价 ✓"，可用 `--ignore "val stubLike = "` 之类把抽出的判据行排除（先跑一次看脚本报的差异行，再决定）。

### 4.4 新增测试（纯 JVM，`PackerAttachFixesTest` + `GuestLoadedApkPathInjectionTest`）

| 用例 | 固定的既有行为 |
| --- | --- |
| `P1` | `com.stub.StubApp` → 判为壳类 |
| `P2` | `com.secneo.apkwrapper.AW` → 判为壳类（含 `wrapper`） |
| `P3` | `mark.via.BrowserApp` → 不是壳类（**不能误伤普通 guest**） |
| `P4` | 大小写不敏感：`STUBAPP` / `ShellApp` → 壳类（原 `ignoreCase = true`） |
| `P5` | 空串 → 不是壳类 |
| `P6` | `/data/data/com.foo/lib` → `<dataDir>/lib`（正则只替换前两段） |
| `P7` | `/data/user/0/com.foo/files/x` → `<dataDir>/files/x` |
| `P8` | 值里含 `com.nextvm.app` → **返回 null（不动）**（原代码的容器自身路径例外） |
| `P9` | 值不含 `/data/data/` 也不含 `/data/user/0/` → null（不改） |
| `I1` | `APP_INFO_FIELDS` 恒为 4 项且顺序固定（漏字段 = 静默失灵，必须铆住） |

**诚实说明**：反射写字段本身不可纯 JVM 测（`android.app.Application` 在单测里是 stub）。
本批收益是"判据可测 + L6 定位成本下降"，不是覆盖率提升。

---

## 五、P3/P4 / B4-4g：framework bind 取证与编排

### 5.1 B4-4g（两个职责，557 行，纯搬迁）

| # | 职责 | 现状 | 去向 |
| --- | --- | --- | --- |
| ① | framework `LoadedApk` 对象图取证 + SecNeo 装载取证（v53–v63） | 顶层 `loadedApkWitness` `:1383-1409` + `secNeoLoadProbe` `:1411-1896` | `diag/probe/FrameworkBindProbe.kt`（`object`，`apply(loadedApk)` / `secNeo(packageName, loadedApk, guestClassLoader)`） |
| ② | stock Instrumentation 换装/还原 + 其结论状态 | `:381-414`（含 `stockInitExperiment` / `stockInitNote`）+ `:416-433` | `lifecycle/startup/StockInstrumentationSwap.kt`（object 自持状态，暴露 `note()` / `experiment()`） |

**批内必须登记的技术债**：`secNeoLoadProbe` 是 **486 行单函数**，内含 v53/v55/v56/v58/v59/v60/v61/v63 七代实验。
本批**整段搬**（保证等价检查能过、diff 可审阅），随后 **B4-4g″** 再按"证据/实验/结论"三段内部拆分
（拆分判据是职责，不是行数；每个子函数只回答一个问题）。登记进 `FrameworkBindProbe.md`「约束与坑」。

**若你认为 557 行一批太大**，可插成 g-1（取证）/ g-2（stock swap）两批；两者无相互依赖，顺序随意。

### 5.2 B4-4g′（`bindViaFrameworkLoadedApk`，161 行）

需要注入的只有 2 个（比原文档估的 3–4 个少，因为 `initContentProviders` / `callApplicationOnCreate` 已是薄委托、可直接静态调用）：

| 注入项 | 形式 | 理由 |
| --- | --- | --- |
| `resourceManager`（`injectResourcesIntoContext` / `injectResourcesIntoActivityThread`） | 函数**参数**（普通对象，不是 lambda） | 它是 VAM 的构造依赖，直传最清楚 |
| 记录表写入（`applications[instanceId] = record` / `applications.remove(instanceId)`） | **interface** `GuestRecordSink { fun register(record); fun remove(instanceId) }` | 复盘文档禁用"函数类型参数"（破坏等价检查）；interface 无花括号配对问题 |

`swapInStockInstrumentation` / `restoreInstrumentation` / `disableCheckJniForGuest` / `loadedApkWitness` /
`secNeoLoadProbe` 此时都已是 object → **直接静态调用，零注入**。

日志逐字不变：`framework makeApplication enter/exit` / `framework bind identity …` / `framework makeApplication returned null` /
`Patched framework LoadedApk mPackageName <a> -> <b>` / `Framework LoadedApk bind failed …` / `framework bind FAILED`。

**这批是 `BreadcrumbGoldenContractTest` C4/C7 的直接保护对象** —— 顺序改了必红。

---

## 六、P5 / B4-4h：`bindApplication` —— 现状、功能与「搬出 vs 收敛」（D2 详解）

### 6.1 现状（`0798d4b`，逐段给行号）

`bindApplication` 在 `VirtualApplicationManager.kt:135-338`，**204 行 / 10 个参数**，是全容器**唯一的 guest 启动入口**
（生产调用点只有 1 个：`VirtualEngine.kt:1863`，且**不接收返回值**，只用副作用）。它内部有 4 段：

| 段 | 行 | 做什么（副作用） | 契约（golden） |
| --- | --- | --- | --- |
| **A 入口** | `:147-193` | `DebugFlags.init(virtualContext)`；`DebugTrace.event("bindApplication","enter …")`；**已绑早退**（`:156-163`，`state != TERMINATED` 就返回旧 Application）；4 行 `Timber` 明细；`GuestBreadcrumb.begin(pkg)` + `bind start instance=… appClass=… providers=N frameworkLoadedApk=…`；`WebViewWatch.schedule`；`InitTrace.startInit()`（**硬编码包名** `:191-193`） | **C1**（begin 每进程 1 次）、**C2/C3** 的锚点、**C10**（`bind start` 恰 1 次） |
| **B framework 分支** | `:201-243` | `bindViaFrameworkLoadedApk(...)` → 非 null 则：`GuestBreadcrumb.attachFile` → `patchActivityThreadApplication` → `registerGuestRedirections` → `sandboxManager.ensureAppDirsReady` → `patchFrameworkDataDir` → `registerBreadcrumbCallbacks` → `bind complete (framework path)` → **两条 `javaIo` 统计**（framework 独有，`:226-235`）→ `scheduleGuestStackDumps` → `scheduleHijackTrace` → `GuestBindBackgroundTasks.scheduleObservers` → `return`；null 则记 `framework path returned null, falling back to legacy bind` 后**落穿**到 C 段 | **C4**（makeApplication 只 1 次）、**C6**（onCreate→v142→diagnostics→watchdog）、**C7**（`bind complete` 只 1 次）、**C10**、**C12** |
| **C legacy 分支** | `:245-329` | `buildApplicationInfo` → `createApplication` → 建 `ApplicationRecord` 入表 → `virtualContext.setGuestApplication` → `swapInStockInstrumentation` → `attachBaseContext`（**前后各一条 breadcrumb**）→ `patchLoadedApkApplication` → `initContentProviders` → `callApplicationOnCreate` → `PluginProbes.probePluginLoaderState` → `restoreInstrumentation` → **与 B 段相同的 7 行收尾** → `bind complete (legacy path)` → 3 个调度 | **C5**（provider 先于 onCreate）、**C12**（`legacy attachBaseContext enter/exit` 各 1 次） |
| **D 兜底** | `:331-337` | `restoreInstrumentation` + `GuestBreadcrumb.logThrowable("bind FAILED …")` + `Timber.e` + `applications.remove` + 返回 null | 崩溃可诊断性（L3 的观测面） |

**关键事实（决定 D2 怎么选）**：

1. **两条路径的收尾序列重复 7 行**：`:216-223,236-238`（framework）与 `:317-328`（legacy）除 framework 独有的 2 条 `javaIo` 统计外**逐字相同**；历史事故 `00673c9`→`612cef5`（"只挂到 framework 路径"当天被 revert，`docs/输出/变更/20261126-B4-4c启动链拆分.md:35-36`）就是这份重复造成的。
2. **编排的唯一性**：它不做任何"可复用计算"，只做"顺序 + 分支 + 收尾"；真正干活的 13 个动作里，10 个已经是薄委托或 object（`ApplicationInfoFactory` / `GuestApplicationFactory` / `GuestAttach`(待建) / `LoadedApkApplicationPatch` / `ContentProviderInitializer` / `ApplicationOnCreateInvoker` / `ActivityThreadApplicationPatch` / `GuestDiagnosticsRegistration` / `GuestStackDumps` / `HijackTrace` / `GuestBindBackgroundTasks`）。
3. **状态依赖**：只有 4 处真状态 —— `applications` 记录表（读写）、`sandboxManager`（构造依赖）、`resourceManager`（经 g′ 已注入 framework bind）、`virtualContext.setGuestApplication`。`bootstrap` 级静态工具（`DebugFlags`/`DebugTrace`/`WebViewWatch`/`InitTrace`/`PluginProbes`）不需要注入。
4. **返回值无人使用**：`VirtualEngine.kt:1863` 忽略返回值 → 搬出后**不需要**改调用方语义，但也意味着"搬出"只带来位置变化。

### 6.2 它的功能（一句话 + 三条边界）

> **功能**：把一个已安装的 guest 的 `Application` 从"类"变成"进程内可用的 Application 实例"——按 `frameworkLoadedApk != null` 选装配路径，两条路径走同一套收尾（诊断/重定向/目录/后台任务），失败时回退 legacy。

不负责（边界）：① 不建 ClassLoader（`VirtualClassLoader` 在其上游）；② 不建 `LoadedApk`（framework 侧由 `createFrameworkLoadedApk` 建，见 golden C12）；③ 不负责 Activity 启动（`VirtualEngine.launchApp` + `ActivityThreadHook`）。

### 6.3 两方案对比（含量化）

| 维度 | **A 搬出**（原计划：`startup/BindOrchestrator.kt`，204 行） | **B 收敛**（建议：留在 VAM，抽 `finishBind`） |
| --- | --- | --- |
| VAM 类体行数 | 1314 → **~949**（h 批后；f/f′/g/g′ 再降后 ~700） | 1314 → **~1100**（h 批后；f/f′/g/g′ 再降后 ~830） |
| 需要新增注入 | **+2～3**：记录表（`GuestRecordSink`）、`ensureAppDirsReady`（或整个 `sandboxManager`）；若 g′ 已搬则 `registerGuestRedirections` / `patchFrameworkDataDir` 还需 2 个 | **0** |
| 调用方改动 | `VirtualEngine.kt:1863` 改指向新类；VAM 是否留薄门面要再决定（留=两层） | **0**（签名不变） |
| 7 行重复是否消除 | ❌ 不消除（除非同时在搬出的文件里做收敛 → 同一批混两种变更，违反规则 10） | ✅ 消除 |
| 规则 10 等价检查 | 需对 204 行整体 `--ignore` 掉 2–3 处注入 → **证明力明显下降**（"逻辑没变"只能靠 golden） | 搬运部分仍可逐字等价；收敛部分（7 行合并）由 **C4/C7/C12 三道契约**兜底 |
| 规则 7「职责单一 / 命名确切」 | 搬出后 VAM = 记录表 + 生命周期 + query + 薄委托，命名仍确切 | 同左，区别只是"编排"这个职责留在 VAM 里（VAM 本来就是"管理 guest Application 的生命周期"，编排属于它） |
| 失败半径 | 把启动入口搬到新类，**diff 期任何顺序错都直接打断启动链**（C4/C5/C7/C10 同时红） | 只有 `finishBind` 一处的顺序风险，出错时 C6/C7 会精确指出 |
| 净收益 | 文件更短（-204） | 消除重复 + 把"两条路径收尾"收敛为一处（真正的缺陷源） |

### 6.4 理由（为什么选 B）

1. **规则 7 的判据是"职责是否单一"，不是"文件是否更短"**。`bindApplication` 现在的描述可以一句话说清（见 6.2），不存在"两个职责混在一起"；搬出只是把同一职责换个文件，**不产生职责划分上的收益**，却要付出 2–3 处注入 + 调用方改动的代价。
2. **真正的问题是 7 行重复，而它只有"在 VAM 内收敛"或"搬出后再收敛"两条路**；后者会把"等价搬迁"和"语义改造"塞进同一批，违反规则 10 与 18.2（不许把顺手优化藏进重构）。
3. **证据基础**：框架路径/legacy 路径的收尾顺序已被 golden **C4/C6/C7/C12 + C10 计数**锁死；收敛 `finishBind` 后若顺序有任何变化，契约测试立刻红 —— 这比"等价检查 + ignore 清单"证明力更强。
4. **诚实说明（修正首版计划里的夸张表述）**：搬出后 VAM 并不会变成"空壳"，仍剩 ~700–950 行（`initialize` / 记录表 / `initContentProviders` / `startWatchdog` 74 行 / `terminateApplication` / 生命周期与 query / 诊断入口）。所以 D2 的选择不是"救 VAM 于空壳"，而是**"用最小的 diff 消除真实重复"**。

### 6.5 收敛的具体做法（B4-4h 的实际 diff）

```kotlin
/** 两条 bind 路径共用的收尾（顺序即契约：C6 → C7）。 */
private fun finishBind(
    application: Application,
    instanceId: String,
    packageName: String,
    path: String,              // "framework path" / "legacy path"
) {
    GuestBreadcrumb.attachFile(application.filesDir)
    patchActivityThreadApplication(application)      // 必须早于 registerBreadcrumbCallbacks（WebViewWarmup）
    registerGuestRedirections(application, instanceId, packageName)
    sandboxManager.ensureAppDirsReady(instanceId, packageName)
    patchFrameworkDataDir(application, packageName)
    registerBreadcrumbCallbacks(application)
    GuestBreadcrumb.log("bind complete ($path)")
    scheduleGuestStackDumps(packageName)
    scheduleHijackTrace(packageName)
    GuestBindBackgroundTasks.scheduleObservers(packageName, application.filesDir)
}
```

**不能合并进 `finishBind` 的两处**（否则 golden 立刻红）：

| 项 | 位置 | 为什么必须留在原处 |
| --- | --- | --- |
| 2 条 `javaIo` 统计（`DebugTrace.event("javaIo", …)`） | framework 段 `:226-235` | **legacy 路径没有这两行**；塞进共享函数会让 legacy golden 多出两条事件（C11 逐行相等会红） |
| `patchActivityThreadApplication` 与 `GuestBreadcrumb.attachFile` 的先后 | framework `:212-216` vs legacy `:317-319` | framework 是"先 attachFile 再 patch"，legacy 是"onCreate 之后才 attachFile"；两者**当前顺序不同**（L5 相关），收敛时**不许顺手对齐**（那是行为变更） |

### 6.6 若你仍选择「搬出」——最小可行做法

1. 新建 `lifecycle/startup/BindOrchestrator.kt`，`object`；参数 = 现有 10 个 + `records: GuestRecordSink`（interface，不用 lambda）+ `onEnsureAppDirs: (String, String) -> Unit`（若 f′/g′ 还没搬完则再加 2 个回调）；
2. `VirtualEngine.kt:1863` 改为 `BindOrchestrator.bind(records = applicationManager.records, …)`；
3. **同一批不做 `finishBind` 收敛**，另开 B4-4h′；
4. 等价检查：204 行整体跑 `check-refactor-equivalence`，把 3 处注入行 `--ignore` 并在镜像文档「偏差登记」逐条写明。

---

## 七、测试计划（分层 + 每批门禁）

### 7.1 五层，每批都要过

| 层 | 是什么 | 命令 | 判据 | 耗时 |
| --- | --- | --- | --- | --- |
| **T0 静态** | 括号/KDoc/跨文件 private/import/重复 import/测试名 | `kotlin-lint.mjs`、`check-imports.mjs`、`check-duplicate-imports.mjs`、`check-test-fn-names.mjs`、`check-diag-channels.mjs`、`check-probe-ids.mjs`、`gen-diag-channels --check`、`gen-breadcrumb-golden --check` | 全过（exit 0） | 毫秒 |
| **T1 等价** | 重构未改逻辑（规则 10） | `check-refactor-equivalence.mjs`（每个被搬函数一次） | 等价 ✓，或偏差已登记 | 秒 |
| **T2 单测** | 新增纯逻辑铆钉 + 既有 119 条不回归 | `module-test.yml`（`test_filter` 到包） | 绿 + `tests>0`（假绿守卫） | ~2 min |
| **T3 契约** | golden × C1–C12 × 8 样本 | 随 T2 一起跑（`BreadcrumbGoldenContractTest`，资源在 `test/resources/breadcrumb-golden/`） | C1–C12 全绿 | 含在 T2 |
| **T4 真机** | 容器+guest 真实启动 | `clean-install-and-test.ps1 -Apk dist\c.apk`（APK 来自 `container-build.yml` 产物） | `SELF_CHECK ok=8 fail=0`；探针 `entries=98 fail=2(J26,J27)`；via `bind complete` + `onActivityResumed` | ~2 min + 构建 3–5 min |

**判定纪律（复盘文档 §二.1）**：真机判定**只读文件**（`files/checks.log`、sandbox 实例目录、
`breadcrumb-<pid>.log`、`probe-report.json`）；容器启动**固定等 ≥10s**；guest 启动只轮询 golden 要素（≤10s）。

### 7.2 每批测试清单（按批填）

| 批 | T2 新增用例 | T3 关注 | T4 关注 |
| --- | --- | --- | --- |
| **B4-5a (L1)** | `StubRegistryTest`：`R1` 释放后同 slot 仍可解析、`R2` 释放只影响该 slot、`R3` `releaseAllStubsForSlot` 对空池是 no-op（模拟"重复 STOP"）。**注意**：`runningApps` 槽位守卫在 `VirtualEngine`（含 Android 依赖）→ **不可纯 JVM 测**，由真机「交替实例」验证（§3.3） | 无需（不碰 bind） | **STOP→LAUNCH ×6 不报 `No available stub`**；**交替实例不抢 stub**（§3.3 两条） |
| **B4-4f** | `PackerAttachFixesTest` P1–P9、`GuestLoadedApkPathInjectionTest` I1 | C1–C3、C9–C11（顺序未变） | via / probe 各一次 `bind complete`；注入日志仍在 breadcrumb（`Injected guest paths into LoadedApk` / `Injected mLibDir`） |
| **B4-4f′** | 新增 `CheckJniGuardTest`：`C1` sdk 34 需关、`C2` sdk 35 跳过、`C3` sdk 36 跳过 | **C12（legacy `legacy attachBaseContext enter/exit` 位置）** | legacy 路径（`setprop debug.nextvm.frameworkBind 0`）启动一次，`bind complete (legacy path)` |
| **B4-4g** | 无纯 JVM 判据 → 镜像文档写不可测原因；**补一条"取证日志前缀常量"用例**（`loadedApkWitness` 的输出格式由字符串断言固定，防格式漂移把 golden 打红） | C4 / C7 | framework 路径两次；`v53 probe` / `v52 loadedApk=` 行仍在 |
| **B4-4g′** | 无（纯编排）；`GuestRecordSink` 的 interface 由 compiler 保证 | **C4 / C7 / C12** | framework 路径 + legacy 回退（`framework path returned null, falling back to legacy bind` 场景由 probe 触发） |
| **B4-4h** | 无新增；既有 119 条 + 契约不得回归 | **C4 / C7 / C12（本批的主护栏）** | via 三项（启动 / 主页 / 长按）+ 探针 J 系列不回退 |

### 7.3 真机回归的最小集（每批 ≥5 项，规则 16）

```
1. SELF_CHECK：files/checks.log → hookcheck ok=8 fail=0
2. 栈转储：files/stackdump-<pid>.log 存在且 > 0 字节（B4-4d 之后一直靠它兜底）
3. 宿主写入：探针 J26/J27 仍是 fail（设计故意）；其余 J 项不回退
4. 豁免名单：SPOOF_EXEMPT_LIST 与 via 零欺骗仍成立（breadcrumb 里 `v142-redirect registered rules=11`）
5. breadcrumb 尾部：`bind complete (<path>)` + `cb onActivityResumed`
```

---

## 八、每批交付物 checklist（规则 7 / 9 / 10）

- [ ] 代码（≤2 职责；新函数**不用函数类型参数**、**不给 `= {}` 默认值**）
- [ ] `*Test.kt`（纯 JVM；不可测的在镜像文档「约束与坑」写明原因）
- [ ] `docs/代码镜像/<同路径>.md`（四节：职责 / 函数 / 偏差登记 / 约束与坑）
- [ ] `docs/输出/变更/<日期>-<批名>.md`（计划 + `## 实际变更` + `.actual.diff`）
- [ ] T0 八项 + T1 等价 + T2/T3 CI 绿 → 才开下一批
- [ ] 能力状态变化同步 `docs/现状/README.md` 与 `STATUS.md`（规则 6）
- [ ] 提交用明确路径（**不要 `git add -A`**；`tools/tmp/` 不入库，规则 17）

---

## 九、本次不做（明确边界）

1. **不动 L3–L6 的语义**（legacy 崩溃、provider 不一致、`mInitialApplication` 身份、legacy 三段 patch 在 framework 缺失）——
   拆分只做等价搬迁；L6 的三段代码本批**只是搬位置**，不改变"只在 legacy 执行"这一现状。
2. **不修 `InitTrace` 的硬编码包名**（登记即可）。
3. **不动 `secNeoLoadProbe` 的内部结构**（g 批整段搬，内部拆分排 g″）。
4. **不碰 WebView `file://` MIME**（`STATUS` 未决 #6，需要另外立项）。
5. **不做全量回归**（规则 16：留到重构收尾）。

---

## 十、执行顺序一眼图

```
B4-5a (L1 修复 + 单测)  ──T0/T2/T4──►  绿
   │
   ├─► B4-4f  (路径注入 + 壳修正)              ──► 绿
   │      └─► B4-4f′ (attach 编排 + CheckJNI)  ──► 绿   ← L6 的代码落点已就位
   ├─► B4-4g  (取证 + stock swap)              ──► 绿
   │      └─► B4-4g′ (framework bind 编排)     ──► 绿
   │             └─► B4-4h (bindApplication 收敛) ──► 绿   ← B4-4 收尾
   └─► B4-5b (L2 修复) → **L11**（`Standard00` 重叠，§3.4）→ L3/L4/L5/L6 修复（各自走变更流程）
```

预计 B4-4 收尾时：VAM **1 896 总行 → 约 700 行**（类体只剩 `initialize` / 记录表 / `initContentProviders` /
`startWatchdog` / 生命周期与 query 方法 / `bindApplication` 收敛后编排），累计从 4 589 起算 **约 −85%**。
