# 变更：process slot 会话复用 + Process Slots UI 解绑（2026-11-27）

- 状态：**计划，等确认后执行**（落盘即停，不动代码）
- 本轮范围：**只修这一个问题**（B4-4 重构暂停；但按「为重构做设计准备」拆成可原样迁移的文件）
- 关联：`STATUS.md` §2、`docs/计划/计划-启动链已知缺陷（重构后修复）.md`（L1/L2）、
  `docs/计划/计划-接管后下一步（L1修复 + B4-4f-g-h）与测试计划.md` §3（L1 副作用研究）

---

## 一、目标（用户口述的真实用例，直接作为验收用例）

**期望行为**

| 用例 | 步骤 | 期望 |
| --- | --- | --- |
| **U1** | 点容器 → 点 guest → 三键 Home → 再进容器 → 点同一个 guest | **回到原来那个进程/原来的界面**（复用，不新起进程、不新起 Task） |
| **U2** | 连续 U1 做 6 次 | 全程正常，**不出现** `No available stub`；slot 数不增长 |
| **U3** | guest 自己退出（被系统回收 / 主动退出） | 容器**清理**它占的 slot：进程号、任务、stub、记录都归还 |
| **U4** | 设置页看 "Process Slots" | 计数 = **真正在跑的 slot 数**（不是"有几个实例标记 isRunning"） |
| **U5** | 点 "Process Slots" | 列出**真正在运行**的 slot 行（slot / guest / pid / 当前界面），点某行**能直接进入该实例当前状态** |

**现状（用户实测）**：U1 得到的是**新进程**（旧进程还在但进不去）→ U2 点 5 次后 `No available stub`；
U4 显示 `1 / 10 in use`（与"开了 5 个进程"不符）；U3 不知道容器做了什么（无回收、无日志）。

---

## 二、现状链路与根因（每条带证据）

### 2.1 链路（点击 guest 到底发生了什么）

```
LauncherScreen 点击 → LauncherViewModel.onIntent(LaunchApp)          feature/launcher/.../LauncherViewModel.kt:107,138-141
  → engine.launchApp(instanceId)                                     VirtualEngine.kt:601
      ├ 每次调用都 stubRegistry.resolveStub(slot, launchMode)         :631        ← 每次消耗一个**新** stub 名
      ├ Intent.setClassName(host, stubClass) + FLAG_ACTIVITY_NEW_TASK :637-645
      ├ serviceManager.activityManager.startActivity(..., stubClassName)  :668
      └ context.startActivity(intent)                                  :712
  → 系统按 stub 组件的 android:process=":pN" 起/复用进程；按 **taskAffinity** 建 Task
```

### 2.2 根因（4 条，互相叠加）

| # | 根因 | 证据 | 造成哪个现象 |
| --- | --- | --- | --- |
| **R1** | **每次点击都分配新 stub**，没有"该实例已有会话"的概念 | `VirtualEngine.kt:631`（唯一 resolve 点）；全仓没有"按 instanceId 记住 stub"的结构 | 每次点击落到**不同组件** |
| **R2** | **每个 stub 有独立 `taskAffinity`**（`…vt.p1.s00` / `s01` …）+ `FLAG_ACTIVITY_NEW_TASK` | `app/src/main/AndroidManifest.xml:149-153`（`.stub.P1$Standard0x` 各自 `taskAffinity` 不同） | 每次点击都**新建 Task**；旧 Task 留在后台 → 用户看到"进入的是新界面"，旧界面"进不去" |
| **R3** | **stub 池只借不还**：`releaseStub` / `releaseAllStubsForSlot` 生产零调用 | `StubRegistry.kt:89-101`；`docs/现状/零调用扫描报告.md:227-228` | 每 slot 5 个 standard stub 用完后 `No available stub`（`:634`）→ 用户说的"点 5 次就没 slot 了" |
| **R4** | **状态/计数的事实源不对**：`usedSlots = apps.count { isRunning }`，`isRunning` 只在 `launchApp` 置 true、`stopApp` 置 false，**没有进程存活探测** | `SettingsViewModel.kt:115`；`VirtualEngine.kt:693,749`；`updateAppState` 只改内存（`:1508-1515`）| U4 显示 `1/10`；进程自己退出后记录仍是"运行中"（U3 无回收） |

> **为什么 `stopApp` 修不好 U1**（对应上一轮计划的 D1）：L1 的"归还"只解决 R3 的一半（让池子可循环），
> **不解决 R1/R2**（每次点击仍会拿一个新的 stub → 仍会新开 Task）。用户要的是"回到原来那个进程"，
> 所以本轮的主修点是 **R1+R2**，R3 用"复位"兜底，R4 用"探测"兜底。

### 2.3 一个必须知道的前提：进程名在 guest 进程里被改过

- guest 进程启动后 `Process.setArgV0(packageName)` + `nativeHookBridge.setProcessName(packageName)`（`VirtualEngine.kt:1811-1824`）
  → **内核 argv0/comm 变成 guest 包名**；
- 但系统侧（AMS）记录的进程名仍是 `com.nextvm.app:pN`（manifest 的 `android:process`）；
- 因此"探测存活"有两个独立通道，**主进程侧都用得到**：
  ① `ActivityManager.getRunningAppProcesses()` → 主进程（零 hook）能看到自己的 `com.nextvm.app:pN`
  ② `ActivityManager.getAppTasks()` → 拿 task、`taskId`、`baseIntent`（含 stub 组件名与 `_nextvm_*` extras）

---

## 三、修复设计（分层，职责单一，可直接作为重构后的目标结构）

### 3.1 五层职责

| 层 | 新文件（包） | 职责 | 能否纯 JVM 测 |
| --- | --- | --- | --- |
| **① 领域模型** | `core/virtualization/process/SlotStatus.kt` | `SlotStatus`（slot / state / instanceId / pkg / pid / taskId / topComponent / note）；`SlotState = FREE / RUNNING / STALE / ORPHAN / CONFLICT` | ✅（纯数据 + 枚举） |
| **② 租约** | `core/virtualization/process/SlotLeaseRegistry.kt` | "实例 ↔ slot ↔ stub ↔ taskId"的**唯一事实源**：`acquire / byInstance / bySlot / release / clear` | ✅（纯 Kotlin，无 Android 依赖） |
| **③ 判定** | `core/virtualization/process/SlotReconciler.kt` | **纯函数**：把（租约、系统进程、系统任务、VAM 记录）四份输入算成 `List<SlotStatus>` + `ReconcileActions`（该清理谁、该保留谁） | ✅ **核心判据全在这里** |
| **④ 探测** | `core/virtualization/process/SlotStatusProbe.kt` | Android 侧取原始数据：`getRunningAppProcesses()`（筛 `hostPkg:` 前缀）、`getAppTasks()`（解析 stub→slot、extras→instanceId、numActivities、`isVisible`） | ❌（Android API），薄到 30–50 行 |
| **⑤ 引擎门面** | `VirtualEngine`（改） | `launchApp` 增加"会话复用"分支；新增 3 个只读/动作 API 给 UI | ❌（依赖 engine） |

### 3.2 关键语义（写进镜像文档「函数」/「约束与坑」）

**① 打开/切回（`launchApp` 的新分支，U1 的核心）**

```
launchApp(instanceId):
  probe = SlotStatusProbe.snapshot(...)         // 系统事实
  rec   = SlotReconciler.reconcile(leases, procs, tasks, records)
  applyCleanup(rec)                             // STALE / ORPHAN 先回收（U3）
  lease = leases.byInstance(instanceId)
  if (lease != null && rec.statusOf(lease.slot).state == RUNNING && task 仍有 activity) {
      appTask.moveToFront()                     // ★ 复用：不分配 stub、不新建 Task
      return Success("brought to front (slot N, task T)")
  }
  // 否则：新会话
  stub = stubRegistry.resolveStub(slot, mode)   // 分配并记入租约
  leases.acquire(instanceId, slot, stub)
  startActivity(...)                             // 原逻辑不变
```

**② 会话结束（归还）**

| 触发 | 动作 | 谁做 |
| --- | --- | --- |
| 用户 STOP | `stopApp`：现有清理 + `leases.release(instanceId)` + `stubRegistry.releaseAllStubsForSlot(slot)`（**同 slot 无其它租约时**） | `VirtualEngine.stopApp` |
| 进程自己没了 | `reconcile` 发现"租约在、进程不在" → `STALE` → 释放租约 + 归还 stub + 清 `activityStack`/`processRecords`/`runningApps` + `isRunning=false` | `reconcile` + `applyCleanup` |
| 任务没了但进程在 | `task.numActivities == 0` → 视为可复用但需重新 startActivity（不新建 stub，**沿用原 stub**） | `launchApp` 分支 |
| 有进程但无租约 | `ORPHAN` → **只报告 + 提供显式清理**（`cleanupOrphan(slot)`），**不自动杀**（避免误杀正在 bind 的新进程） | UI 按钮 / `cleanupStaleSlots` |

**③ 探测时机（不做常驻轮询）**

1. `launchApp` 开头（每次点击）；2. Settings 页 `Refresh`/进入 Process Slots 页；3. `stopApp` 末尾。
不常驻轮询：省电、且这三处已覆盖"用户会看到的状态"。

### 3.3 UI 解绑（用户要求：UI 只发起请求和查看结果）

**硬约束（写进本文件，后续 review 用）**

| 规则 | 说明 |
| --- | --- |
| UI（Composable）**只能**：① 渲染 `UiState`；② 调 `viewModel.onIntent(X)` | 不 import `VirtualEngine`、不 new `Intent`、不读文件、不调 `ActivityManager` |
| ViewModel **只能**：把 intent 翻译成一次引擎调用，并把结果写回 `UiState` / 发 `SettingsEffect.ShowMessage` | 不推断状态（"我猜它在跑"），一律以引擎返回为准 |
| 引擎（`VirtualEngine`）**只暴露**语义化 API，不暴露内部集合 | `slotStatus()` / `bringToFront(instanceId)` / `cleanupSlot(slot)` |

**数据流**

```
ProcessSlotsScreen ──onIntent(RefreshSlots)──► SettingsViewModel ──engine.slotStatus()──► List<SlotStatus>
        ▲                                              │
        └────────── uiState.slots ◄────────────────────┘
ProcessSlotsScreen ──onIntent(BringToFront(instanceId))──► ViewModel ──engine.bringToFront()──► VmResult
ProcessSlotsScreen ──onIntent(CleanupSlot(slot))─────────► ViewModel ──engine.cleanupSlot(slot)──► VmResult
```

**UI 文案（Setting 页那一行）**

```
Process Slots        3 / 10 in use        ← usedSlots 改为"真有进程且在租约里的 slot 数"
```

列表页每行（`ProcessSlotsScreen`）：

```
[p1]  mark.via           pid 12345   RUNNING   task 42 · mark.via.Shell     [进入]  [清理]
[p3]  com.sandbox.probe  pid 12399   RUNNING   task 45 · ProbeActivity      [进入]
[p2]  —                  —           STALE     (进程已退出，待回收)          [清理]
[p4]  —                  —           ORPHAN    (进程/任务无租约)             [清理]
```

### 3.4 状态语义与发生路径（ORPHAN 从哪来、D4 那种情况是什么）

这一节是为了避免"判定写得对、现场看不懂"。五种状态里只有 `STALE` / `ORPHAN` 需要解释"怎么发生的"。

#### 3.4.1 `ORPHAN`（进程在、无租约）的四条发生路径

**前提**：租约表是**容器主进程的内存态**（`@Singleton`），而 `:pN` 是**系统进程**，两者生命周期不同步 —— ORPHAN 就是这个不同步的可见形态。

| # | 路径 | 机制（代码事实） | 频率 | 是否能自动处理 |
| --- | --- | --- | --- | --- |
| **O1** | **容器主进程被系统回收后重建** | 租约表随主进程消失；`:pN` 进程是独立进程，LMK 杀主进程不会连带杀它（若 guest 前台/有服务更不会被杀）。重建后的主进程首次 reconcile 就看到"有 `:pN`、无租约" | **必然发生**（回桌面待久了再进容器） | ❌ 不能自动杀：那个 guest 可能正被用户使用 |
| **O2** | **guest 侧自己起了进程，绕过 `launchApp`** | ① FCM/广播拉起服务：`getServiceStubForApp` 硬编码 `P<slot>$S00`（`ActivityManagerProxy.kt:1139-1142`）；② guest 内部 `startActivity` 用 `P<slot>$Standard00`（`:1134-1137`）；③ DeepLink。这些路径**都不经过** `VirtualEngine.launchApp`（`:601-719`），因此**不会登记租约** | 常见（有推送/常驻服务的 guest） | ❌ 同上；但可以后续补"服务也登记租约"（见 §十一 的缺口） |
| **O3** | **会话结束但进程没杀干净** | `stopApp` 已 `SIGKILL`（`:754-757`），但 `GuestProcessStopper.ok=false`（pid 没扫到 / 无权限 / 时间差）时进程残留；租约已释放 | 偶发 | ✅ 可自动（此时无租约，杀它不伤用户）—— 但仍先按 §四 的"只报告"起步，收集一轮实测后再决定是否自动化 |
| **O4** | **`uninstallApp` 后进程残留** | `uninstallApp → stopApp`（`:768`）释放 slot 与租约；杀进程失败时进程留着，而实例已从安装表删除 | 偶发 | ✅ 可自动（实例已不存在，无"进入"语义） |

**为什么起步阶段"只报告不自动杀"**：O1/O2 的进程**正被用户使用**（前台界面就在那儿），自动杀等于"点一下设置页就把用户的 app 杀了"。所以策略分级：

| 状态 | 自动动作 | 手动动作 |
| --- | --- | --- |
| `STALE`（有租约、无进程） | **自动回收**（无用户可见损失） | "清理"（幂等） |
| `ORPHAN`（无租约、有进程） | **不杀**，只在列表里显示并标 `note=orphan(o1/o2/o3)` | "清理"→ 杀进程 + 清残留 |
| `CONFLICT`（同 slot 多实例） | 不处理 | 显示 + 提示（L2 修好后消失） |

#### 3.4.2 D4 那种情况：「进程在，但 task 里没有 Activity」

**场景**：guest 内部一路按返回，把最后一个 Activity `finish()` 掉。此时：

1. **进程还在**：Android 不会因为 Activity 归零立刻杀进程（先变 cached），有前台服务/常驻线程时更可能长期存活；
2. **task 通常会消失**：最后一个 Activity finish → 系统移除该 Task 记录（ROM 差异下可能残留一个空 task）。于是 `getAppTasks()` 里查不到它，或查到 `numActivities == 0`；
3. 结果：**有租约 + 有进程 + 无 task**。

**这时"点 guest 图标"应该是"回到同一个进程、重新拉起界面"，而不是"新建会话"**，依据是三条代码事实：

| 事实 | 位置 | 含义 |
| --- | --- | --- |
| 每个 stub 的 `android:process=":pN"` 固定 | `AndroidManifest.xml:149-153` | 只要 `:pN` 还活着，系统在启动该 stub 组件时**复用同一进程** |
| 进程内 Application 只 boot 一次 | `ensureGuestApplicationBooted` 的早退：`applicationManager.getApplication(instanceId) != null` → return（`VirtualEngine.kt:1660-1665`） | 复用会话**不会**重跑 `bindApplication`，登录态/静态状态都保住 |
| stub 的 `taskAffinity` 固定 | 同上 manifest | 用**同一个** stub 重新 `startActivity` → 落回同一个 affinity 的 Task（不存在则新建同名 Task），不会像 R2 那样又开一个 `s01` 的新 Task |

**所以 D4 的处置**：`launchApp` 在"有租约 + 进程在 + 无可用 task"时 → **用租约里记的原 stub 类名**重新 `startActivity`（走原有 `:637-712` 逻辑），**不调 `resolveStub`、不 acquire 新租约**。

> 与"新建会话"的唯一区别就是"用哪个 stub 类名"。复用原 stub 才满足"同一进程 + 同一 affinity"，这是 U1 在"Activity 已全 finish"这种边界下仍然成立的关键。

---

## 四、影响范围

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `core/virtualization/.../process/SlotStatus.kt` | 新增 | 状态模型（纯数据） |
| `core/virtualization/.../process/SlotLeaseRegistry.kt` | 新增 | 租约表（纯 Kotlin） |
| `core/virtualization/.../process/SlotReconciler.kt` | 新增 | 纯判定函数（核心可测点） |
| `core/virtualization/.../process/SlotStatusProbe.kt` | 新增 | Android 探测（薄） |
| `core/virtualization/.../engine/VirtualEngine.kt` | 修改 | `launchApp` 复用分支；新增 `slotStatus()` / `bringToFront()` / `cleanupSlot()`；`stopApp` 归还租约与 stub |
| `core/virtualization/src/test/.../process/SlotReconcilerTest.kt` | 新增 | S1–S10（纯 JVM） |
| `core/virtualization/src/test/.../process/SlotLeaseRegistryTest.kt` | 新增 | L1–L6（纯 JVM） |
| `feature/settings/.../SettingsViewModel.kt` | 修改 | 新 intent：`OpenProcessSlots` / `RefreshSlots` / `BringToFront` / `CleanupSlot`；state 加 `slots` |
| `feature/settings/.../SettingsScreen.kt` | 修改 | "Process Slots" 行改为可点击 + 计数取 `uiState.usedSlots`（引擎给的真值） |
| `feature/settings/.../ui/ProcessSlotsScreen.kt` | 新增 | 只渲染 + 发 intent |
| `app/.../MainActivity.kt` | 修改 | 新增路由 `composable("settings/process_slots")` |
| `feature/settings/.../SettingsScreen.kt` | 修改 | 加 `onNavigateToProcessSlots` 参数（与既有 `onNavigateBack` 同风格） |
| `docs/代码镜像/core/virtualization/process/*.md` | 新增 | 4 份镜像文档（四节） |
| `docs/现状/README.md` / `STATUS.md` | 修改 | 能力状态（规则 6） |

**不碰**：`bindApplication` / `attachBaseContext` / native / WebView / 路径重定向 / golden 契约。

---

## 五、逐文件 diff 预览（关键签名，非最终实现）

### 5.1 `SlotStatus.kt`（新，纯数据）

```kotlin
enum class SlotState { FREE, RUNNING, STALE, ORPHAN, CONFLICT }

data class SlotStatus(
    val slot: Int,
    val state: SlotState,
    val instanceId: String? = null,
    val packageName: String? = null,
    val pid: Int = 0,
    val taskId: Int = 0,
    val topComponent: String? = null,   // 系统侧看到的是 stub 组件名
    val note: String = "",
)
```

### 5.2 `SlotLeaseRegistry.kt`（新，纯 Kotlin，可测）

```kotlin
data class SlotLease(val slot: Int, val instanceId: String, val stubClassName: String,
                     val acquiredAtMs: Long, val taskId: Int = 0)

class SlotLeaseRegistry {
    fun acquire(slot: Int, instanceId: String, stubClassName: String): SlotLease   // 同实例重复 acquire 返回既有（幂等）
    fun byInstance(instanceId: String): SlotLease?
    fun bySlot(slot: Int): SlotLease?
    fun release(instanceId: String): SlotLease?                                    // 幂等
    fun all(): List<SlotLease>                                                     // 按 slot 排序，UI 直接可用
    fun noteTaskId(instanceId: String, taskId: Int)                                // 首次绑定时回填
}
```

### 5.3 `SlotReconciler.kt`（新，**核心判据，纯函数**）

```kotlin
data class ProcSnapshot(val slot: Int, val pid: Int)
data class TaskSnapshot(val taskId: Int, val slot: Int?, val instanceId: String?,
                        val numActivities: Int, val visible: Boolean, val topComponent: String?)

data class ReconcilePlan(
    val statuses: List<SlotStatus>,          // 供 UI
    val releaseLeases: List<SlotLease>,      // 要归还的（STALE）
    val releaseStubsForSlots: List<Int>,     // 要归还 stub 的 slot
    val markNotRunning: List<String>,        // 要清 isRunning 的实例
    val orphans: List<SlotStatus>,           // 只报告
)

object SlotReconciler {
    fun reconcile(
        leases: List<SlotLease>,
        procs: List<ProcSnapshot>,
        tasks: List<TaskSnapshot>,
        records: List<RecordSnapshot>,       // VAMS 的 instanceId → state
        maxSlots: Int,
    ): ReconcilePlan
}
```

**判定规则（就是 UI 上那五种状态）**

| 状态 | 条件 | 动作 |
| --- | --- | --- |
| `RUNNING` | 有租约 + 该 slot 有进程 + 有 task（`numActivities>0`） | 保留；UI 显示"进入" |
| `RUNNING`（无 task） | 有租约 + 有进程 + 无 task | 保留租约；下次 `launchApp` **沿用原 stub** 重新 `startActivity` |
| `STALE` | 有租约 + **无进程** | 归还租约 + stub + 清记录 |
| `ORPHAN` | 无租约 + 有进程 | 只报告（等待用户点"清理"） |
| `CONFLICT` | 同一 slot 有 ≥2 个不同实例（L2 的现场） | 保留"最近 acquire"的那个，另一个标 CONFLICT 并提示 |
| `FREE` | 无租约 + 无进程 | 不显示（或折叠为"空闲槽 n"） |

### 5.4 `VirtualEngine`（改，见 §3.2 的伪代码）

```diff
     fun launchApp(instanceId: String, activityName: String? = null): VmResult<Unit> {
         ensureInitialized()
         val app = findApp(instanceId) ?: return VmResult.Error("App not found: $instanceId")
+        // 会话复用优先：有活会话就切前台，不消耗 stub、不新建 Task
+        val plan = reconcileSlots()
+        applyReconcilePlan(plan)                       // STALE/ORPHAN 处理
+        val lease = leases.byInstance(instanceId)
+        if (lease != null && bringToFrontInternal(lease)) {
+            return VmResult.Success(Unit)
+        }
+        val stubClass = lease?.stubClassName            // ★ 复用原 stub，避免新 stub / 新 affinity
+            ?: stubRegistry.resolveStub(app.processSlot, launchMode)
+            ?: return VmResult.Error("No available stub for process slot ${app.processSlot}")
+        leases.acquire(app.processSlot, instanceId, stubClass)
```

```diff
+    fun slotStatus(): List<SlotStatus> =
+        runCatching { reconcileSlots().statuses }.getOrElse { emptyList() }
+
+    fun bringToFront(instanceId: String): VmResult<Unit>
+    fun cleanupSlot(slot: Int): VmResult<Unit>      // STALE / ORPHAN 显式清理
```

### 5.5 `SettingsViewModel` / `SettingsScreen` / `ProcessSlotsScreen`（UI 解绑）

```diff
 sealed class SettingsIntent {
+    data object OpenProcessSlots : SettingsIntent()
+    data object RefreshSlots : SettingsIntent()
+    data class BringToFront(val instanceId: String) : SettingsIntent()
+    data class CleanupSlot(val slot: Int) : SettingsIntent()
 }
```

```diff
-                        usedSlots = apps.count { app -> app.isRunning }
+                        usedSlots = engine.slotStatus().count { it.state == SlotState.RUNNING }
```

`ProcessSlotsScreen(onNavigateBack, onBringToFront = { vm.onIntent(BringToFront(it)) }, ...)`：
**没有** `engine` 参数、**没有** `Context.startActivity`，只有 `uiState.slots` + intent 回调。

---

## 六、与既有缺陷的关系（重要：本轮不动重构，但要让 L1/L2/L11 的账对得上）

| 缺陷 | 本轮影响 |
| --- | --- |
| **L1**（stub 只借不还） | **本轮 Step 1 正式修复**（§十一，严格独立提交）；同时 R1 修好后每次点击不再分配新 stub、`reconcile` 也会归还，形成三重兜底 |
| **L2**（两实例共用 slot） | 判定层的 `CONFLICT` 把手动发现的现场变成**可观测的 UI 状态**；本轮**不修分配算法**（那是独立变更），只在 UI 里如实显示 |
| **L11**（`Standard00` 与 guest 内部固定名重叠） | R1 修好后，容器首启仍用 `Standard00`；但由于"不再每次新分配"，同一 slot 上重叠次数从"每次点击"降到"每会话一次"，风险显著下降。L11 仍需独立立项 |
| **M1（新登记）** | `getAppTasks()`/`moveToFront()` 在 **vivo / Android 15** 上是否可用，需真机首测；若受限 → 退化路径见 §七 #3 |

---

## 七、风险与回退

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | `bringToFront` 失败（ROM 限制 `moveToFront`），导致"点击没反应" | 失败时**回退到原路径**（分配新 stub 启动），并 `ShowMessage("无法切回原任务，已重新打开")`；日志记 `bringToFront FAILED <原因>` |
| 2 | `getAppTasks()` 在个别 ROM 只返回部分任务 → 误判"无会话" → 又开新进程 | 判定用**双通道**：进程存在（`getRunningAppProcesses`）+ 租约存在即认为"有会话"；任务只是"能否切前台"的附加信息（`task=0` 时走"沿用原 stub 重新 startActivity"，仍不开新进程） |
| 3 | `ORPHAN` 自动杀会误杀正在 bind 的进程 | **ORPHAN 一律只报告**，杀要用户显式点"清理" |
| 4 | 租约表与真实状态长期漂移 | 每次 `launchApp` 前 + 进设置页都 reconcile；UI 每次打开重新取 |
| 5 | 改 `launchApp` 影响启动链（golden C1–C12） | 复用分支只改"发哪个 stub / 是否 startActivity"，**bind 链本身不动**；golden 契约应保持绿（若红 → 按 18.4 分类） |
| 6 | `AppTask` extras 可能被系统裁剪（`baseIntent` 保留与否） | 不依赖 extras：stub 类名 → slot 的解析是**兜底主路径**（`^com\.nextvm\.app\.stub\.P(\d+)\$`） |

**回退**

```powershell
git -C _ref/ShitJumpingVM revert <本批 commit>
```

新增文件全部`新增`，回退即删除；`VirtualEngine.launchApp` 的复用分支可用一处开关关闭
（`DebugFlags.GUEST_SESSION_REUSE`，默认 true），便于现场二分。

---

## 八、测试计划

### 8.1 纯 JVM 单测（核心判据，先写测试再改代码）

`SlotReconcilerTest`（S 系列）

| 用例 | 固定什么 |
| --- | --- |
| `S1 租约在 进程在 任务在 判 RUNNING` | U1 的正常态 |
| `S2 租约在 进程不在 判 STALE 并要求归还 stub` | U3 的核心 |
| `S3 租约在 进程在 无任务 仍 RUNNING 且不归还` | 进程活着但 Activity 全 finish |
| `S4 无租约 进程在 判 ORPHAN 且不自动清理` | 防误杀 |
| `S5 同 slot 两实例 判 CONFLICT 保留最近者` | L2 可观测化 |
| `S6 FREE 槽不出现在列表` | UI 只显示"在用"的 |
| `S7 STALE 的归还列表包含该 slot 且不含其它 slot` | 归还的粒度 |
| `S8 进程存在但 slot 与租约不符（进程漂移）判 CONFLICT 并给 note` | 探测异常 |
| `S9 maxSlots 边界（slot=9 有效，10 越界不入列表）` | 与 `MAX_PROCESS_SLOTS` 一致 |
| `S10 plan 幂等` | 连续两次 reconcile 的 statuses 相同（防 UI 抖动） |
| `S11 stub 类名解析 slot（含非 stub / 异常前缀 → null）` | 任务/进程 → slot 的解析契约 |
| `S12 进程名解析 slot（主进程 / 非法后缀 / 别的包 → null）` | 同上 |
| `S13 多任务时取第一个有 Activity 的` | 空 task 不参与判定 |

`SlotLeaseRegistryTest`（L 系列）

| 用例 | 固定什么 |
| --- | --- |
| `L1 acquire 后再 acquire 同实例返回同一租约（幂等）` | 防重复分配 |
| `L2 bySlot / byInstance 双向可查` | UI 两种入口 |
| `L3 release 幂等，且只释放指定实例` | 不误伤 |
| `L4 noteTaskId 回填后 all 里可见` | 切前台需要的 taskId |
| `L5 all 按 slot 升序` | UI 稳定顺序 |
| `L6 同 slot 被第二实例 acquire 时不覆盖（交给 CONFLICT 判定）` | 分配算法不在本层 |
| `L7 clear 后全空` | 容器侧会话全丢时的清理语义 |

### 8.2 契约层（不改 golden，只确认没被破坏）

```powershell
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:virtualization" test_filter="com.nextvm.core.virtualization.*"
```

判据：既有 119 条 + `BreadcrumbGoldenContractTest` C1–C12 × 8 样本**保持绿**（bind 链未动，理应不变）。

### 8.3 真机（对应用户的 U1–U5，**判定只读文件/系统状态，不看 logcat**）

| 用例 | 步骤 | 判据 |
| --- | --- | --- |
| **U1 复用** | 点 guest → Home → 再点同一 guest | ① `settings` 页 `usedSlots` 仍为 1；② 进程 pid **不变**（`adb shell ps -A \| grep nextvm.app:p` 前后对比）；③ 界面回到**离开时那页**（不是 guest 首页）；④ breadcrumb **不出现**新的 `bind start` |
| **U2 不耗尽** | U1 连做 6 次 | 每次 `bind start` 次数不增加（仅第一次有）；无 `No available stub` |
| **U3 自愈** | 手动 `adb shell kill <guest pid>` → 进设置页刷新 | 该 slot 变 STALE→被回收；`usedSlots` 减 1；再次点该 guest 能正常启动 |
| **U4 计数** | 同时跑 via + probe | `Process Slots` 显示 `2 / 10 in use`（不是 1） |
| **U5 列表与进入** | 点 `Process Slots` | 列出 2 行（slot/pid/当前界面）；点 via 那行 → **回到 via 当前界面**（pid 不变） |

辅助证据（都读文件/系统状态）：

```powershell
# 进程（系统视角，不受 setArgV0 影响）
adb shell ps -A | findstr "nextvm.app:p"
# 任务（容器自己可见）
adb shell dumpsys activity activities | findstr "nextvm.app/.stub.P"
# 容器侧判定文件（既有约定）
adb shell run-as com.nextvm.app ls files/            # checks.log / debug-trace.log / breadcrumb-*.log
```

---

## 九、为重构做的设计准备（本轮必须交付，不改重构批次）

| 准备项 | 内容 |
| --- | --- |
| 文件边界 | 新增 4 个文件都在 `core/virtualization/process/`，**不依赖** `VirtualApplicationManager` 任何私有成员 → 重构（B4-4f/g/h）时可原样保留 |
| 判据外置 | reconcile 的判定全部在 `SlotReconciler`（纯函数），`VirtualEngine` 只做"取数据 + 执行 plan" → 以后换实现不影响判据 |
| UI 契约 | `SettingsIntent` 是唯一入口（已在 `SettingsViewModel.kt:46` 有先例），新增 4 个 intent 不改既有结构 |
| 文档 | 4 份镜像文档（职责/函数/偏差登记/约束与坑），其中「偏差登记」要写：**为什么用 `getAppTasks` 而不是自建 task 表**（AOSP 里 task 归 AMS，容器不该另建一份真相） |
| 缺口登记 | 本轮产生的 `ORPHAN 不自动清理`、`CONFLICT 只报告不修复`（L2）、`L11` 全部写进 `计划-启动链已知缺陷（重构后修复）.md`；另登记 **O2 缺口**：guest 侧经服务/FCM/DeepLink 起进程不走 `launchApp` → 不登记租约（后续可在 `getServiceStubForApp` 路径补登记，需另一批） |

---

## 十、分步实施（**从轻到重**，每步独立可测、可回退，行为符合设计即通过）

原则：**每步只引入一类行为变化**，前一步的中间态是"设计内的中间态"（不是回归）。
每步固定四件套：① 代码 ② 单测 ③ 镜像文档 ④ 一次 `module-test` + 对应真机用例。

| 步 | 行为变化 | 触及文件 | 中间态是否可接受 |
| --- | --- | --- | --- |
| **Step 0a** | **零行为变化**（纯只读） | `SlotStatus.kt`、`SlotLeaseRegistry.kt`、`SlotReconciler.kt`、`SlotStatusProbe.kt`、`VirtualEngine.slotStatus()`（新，无人调用） | ✅ 完全无变化 |
| **Step 0b** | 只改"看得见"：设置页计数 + Process Slots 列表（**只读**，"进入/清理"按钮禁用） | `SettingsViewModel`、`SettingsScreen`、`ProcessSlotsScreen`、`MainActivity`（路由） | ✅ 只读，最坏情况是"显示的数和以前不一样"——而这正是要修的 |
| **Step 1** | `stopApp` 归还 stub（**= 上一轮 D5 / B4-5a，严格按 §十一 执行**） | `VirtualEngine.stopApp`、`StubRegistryTest` | ✅ 池子可循环；**U1 仍不满足**（点击仍新开 Task），属预期 |
| **Step 2** | 会话复用：`launchApp` 有活会话就走 `moveToFront` / 沿用原 stub | `VirtualEngine.launchApp`、`SlotLeaseRegistry` 接线 | ✅ U1/U2 满足；U3 尚未自愈（进程死了要等 Step 3） |
| **Step 3** | 自愈回收：`STALE` 自动回收、`ORPHAN` 报告、`cleanupSlot()` | `VirtualEngine.applyCleanup` / `cleanupSlot` | ✅ U3 满足 |
| **Step 4** | 列表动作生效：`bringToFront` / `cleanupSlot` 接 UI | `ProcessSlotsScreen` 按钮 → intent | ✅ U5 完整 |

### 10.1 每步的完成判据（DoD）

| 步 | 测试 | 验收（行为符合设计即可） |
| --- | --- | --- |
| **Step 0a** | T0 静态检查 + `SlotReconcilerTest` S1–S10 + `SlotLeaseRegistryTest` L1–L6 全绿 | 单测通过；**真机不测**（无行为变化）；`module-test` 绿 |
| **Step 0b** | 同上 + 手工看 UI | 同时跑 via + probe → 设置页显示 `2 / 10 in use`；列表两行，pid 与 `adb shell ps -A \| findstr nextvm.app:p` **一致**；按钮 disabled |
| **Step 1** | `StubRegistryTest` R1–R3 + 既有 G1–G10 不回归 | **U2 的一半**：`STOP→LAUNCH ×6` 不出现 `No available stub`；守卫生效（同 slot 有第二实例时不释放，见上一轮 §3.3 判据） |
| **Step 2** | 既有单测 + 契约 C1–C12 | **U1**：点 guest → Home → 再点同一 guest，`pid 不变`、无新的 `bind start`、界面回到离开时那页；**U2 完整**：连做 6 次不再有第 2 个 `bind start` |
| **Step 3** | S2/S4/S7 已覆盖；真机 U3 | `kill <guest pid>` → 进设置页刷新 → 该 slot 从 RUNNING → 回收（`usedSlots` 减 1），再点该 guest 能正常起；ORPHAN 行出现但**进程未被杀** |
| **Step 4** | 真机 U5 | 列表点 via 行 → 回到 via 当前界面且 pid 不变；点 STALE 行的"清理" → 该行消失 |

### 10.2 每步的通用执行顺序（不可跳）

```
改代码 → T0 静态(6 项) → 单测（本地或 CI）→ module-test（一次 dispatch）
      → 容器构建（需要真机时，一次 dispatch）→ 真机对应用例 → 绿 → 下一步
```

失败判定一律按规则 18.4：先分「编码错误 / 行为偏移（该不该变）」再动手；**不许为变绿改断言**。

### 10.3 每步的回退单元

| 步 | 回退 |
| --- | --- |
| Step 0a/0b | 新文件删除 + UI 两处 `git checkout --`（不触及引擎） |
| Step 1 | `git -C _ref/ShitJumpingVM revert <step1 sha>` |
| Step 2 | `DebugFlags.GUEST_SESSION_REUSE=0`（保留代码，现场二分）；或 revert |
| Step 3 | 同上，`GUEST_SESSION_REUSE=0` 时 reconcile 只读不清理 |
| Step 4 | UI 按钮 disabled（1 行） |

---

## 十一、Step 1 严格计划（= 上一轮 D5 / B4-5a，正式纳入本轮）

> 用户已允许顺带做，但**严格按本节执行**，不与 Step 2 合并提交。

| 项 | 内容 |
| --- | --- |
| 目标 | `STOP` 后同一 `processSlot` 可反复 `LAUNCH`，不再出现 `No available stub for process slot N` |
| 改动 | `VirtualEngine.stopApp`：在 **`SIGKILL` 之后**（`:754-757` 之后）`stubRegistry.releaseAllStubsForSlot(slot)`，双重守卫：`::stubRegistry.isInitialized` + `runningApps.values.none { it == slot }`；日志 `Released stubs for slot N (killed=k/total)` |
| 为什么在这里 | 位置搬错（放在 `releaseSlot` 后）会**编译不过**（`kill` 尚未定义）且语义变成"杀进程前归还"——上一轮已修正，见 `计划-接管后下一步…` §3.1 |
| 单测 | `StubRegistryTest` 新增 `R1` 释放后同 slot 可再解析、`R2` 只影响该 slot、`R3` 空池释放是 no-op。`runningApps` 守卫在 Android 侧 → 由真机判据覆盖 |
| 真机 | ① `STOP→LAUNCH ×6` 不报错；② 交替实例：`LAUNCH via`→`LAUNCH probe`→`STOP via` 时**不出现**释放日志（守卫），`STOP probe` 才出现；③ `files/checks.log` 仍 `hookcheck ok=8 fail=0` |
| 文档 | `docs/输出/变更/20261127-l1-stub-release.md`（含实际 diff）；`计划-启动链已知缺陷` L1 标记已修；`STATUS.md` §2 #2、`架构-启动链契约.md` §六 #1、`零调用扫描报告.md:228` 同批更新 |
| 已知残余 | `Standard00` 与 guest 内部固定名重叠（L11）——只登记，**不在本步修** |
| 预期中间态 | Step 1 后 U1（点 guest 回到原界面）**仍不满足**，这是设计内的中间态 |

---

## 十二、本次不做

1. **不改 slot 分配算法**（L2：`VirtualProcessManager` 与持久化 `processSlot` 的一致性）—— 只把现场变成 UI 上可见的 `CONFLICT`；
2. **不改 `bindApplication` / 启动链结构**（重构暂停）；
3. **不改 native / 路径重定向 / WebView**；
4. **不做常驻轮询监控**（进程死亡靠"下次交互时探测"）；
5. **不自动杀 ORPHAN**（需用户点"清理"）；
6. **不动 `Standard00` 重叠（L11）**。

---

## 实际变更（Step 0a / 0b，2026-11-27 落地中）

### 容器仓库（`_ref/ShitJumpingVM`，分支 `baseline-v150`）

| commit | 内容 |
| --- | --- |
| `2ba51b1` | **Step 0a**：`SlotStatus.kt` / `SlotLeaseRegistry.kt` / `SlotReconciler.kt` / `SlotStatusProbe.kt` + `SlotReconcilerTest`（S1–S13）+ `SlotLeaseRegistryTest`（L1–L7）+ `VirtualEngine.slotStatus()`（只读，未接线） |
| `cbe6dea` | **Step 0b**：`ProcessSlotsViewModel.kt` / `ProcessSlotsScreen.kt`（只读列表）+ `SettingsViewModel`（计数改用 `slotStatus()`）+ `SettingsScreen`（行可点）+ `MainActivity`（路由 `settings/process_slots`） |

### 主仓库

| 文件 | 类型 |
| --- | --- |
| `docs/输出/变更/20261127-process-slots-会话复用与UI解绑.md` | 新增（本文件） |
| `docs/代码镜像/core/virtualization/process/{SlotStatus,SlotLeaseRegistry,SlotReconciler,SlotStatusProbe}.md` | 新增（四节） |
| `docs/现状/README.md` | 修改（能力表新增"process slot 只读观测 ⚠️ 待真机验证"） |
| `docs/计划/README.md` | 修改（主线改为本任务，B4-4 标暂停） |

### 本地静态检查（Step 0a + 0b 全部通过）

```text
kotlin-lint（11 个文件）✓
check-imports（core 67 文件 / settings 5 文件 / app 7 文件）✓
check-duplicate-imports ✓
check-test-fn-names（两个测试文件）✓
```

### 测试

| 项 | 结果 |
| --- | --- |
| `module-test`（`:core:virtualization`，filter `process.*`，容器 ref `baseline-v150`） | run `45787633674` **全绿**：编译 step `success` + `Run module unit tests` `success` + `Assert tests actually ran` `success`（假绿守卫通过 → 确实跑了用例） |
| `container-build`（`ref=baseline-v150`） | 已 dispatch（0b 的编译验证，产物用于真机 U4/U5-只读） |
| 真机 | **Step 0a 不测**（零行为变化）；Step 0b 只做"看得到"验证（U4 计数、列表 pid 与 `ps` 一致），按钮禁用属预期 |
