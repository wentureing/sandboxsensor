# 架构：slot / 租约 / 进程 / guest 的术语与表述规范（2026-11-27）

- 回答三个问题：
  1. **guest 进程名 = 包名，是否规范？**（对比"包名+编号"）
  2. **租约（lease）、slot、进程、guest 各是什么、什么关系？**
  3. **同时开多个 via、多个 probe 时，怎么表述？**
- 关联：`架构-进程名与包名身份模型.md`、`架构-启动链契约.md`
- **结论（2026-11-27 拍板，见 §五）**

---

## 一、术语表（先定义，再讨论名字）

| 术语 | 定义 | 唯一键 | 谁持有 | 代码 |
| --- | --- | --- | --- | --- |
| **guest 包** | APK 的包名（`mark.via`） | 包名 | APK manifest | `VirtualApp.packageName` |
| **instance（实例）** | 一次"安装"产生的记录：独立数据目录、独立 slot、独立 CL | `instanceId`（`pkg_<epochMillis>`） | `VirtualEngine._installedApps` | `installApp`：`"${packageName}_${System.currentTimeMillis()}"` |
| **slot（槽）** | 进程槽 `0..9`，对应 manifest 的 `:pN` 与 stub 组件族 `P<N>$*` | slot 号 | `VirtualProcessManager` + `SlotLeaseRegistry` | `MAX_PROCESS_SLOTS = 10`（`ProcessSlot.kt`） |
| **process（进程）** | 系统进程（`:pN`），**一个 slot 最多一个** | pid；系统面名 `com.nextvm.app:pN` | 系统 | manifest `android:process=":pN"` |
| **lease（租约）** | 一个 instance 占用一个 slot 的凭证：`slot + stubClassName + taskId(首次)` | `instanceId` | `SlotLeaseRegistry`（主进程内存） | `SlotLease(slot, instanceId, stubClassName, acquiredAtMs, taskId)` |
| **session（会话）** | 一个 stub 组件上的一个运行着界面的窗口（= 一个系统 task） | `taskId` | 系统 AMS（`AppTask`） | `SlotStatus.tasks: List<TaskRef>` |
| **task** | AMS 的 Task：1 个 base Intent（stub 组件）+ N 个 Activity | `taskId` | 系统 | `TaskSnapshot/TaskRef` |
| **stub 组件** | manifest 预声明的替身组件（`P1$Standard00`），带独立 `taskAffinity` | 类名 | manifest + `StubRegistry` | `StubRegistry.candidatesFor()` |

### 数量关系（不变量）

```
slot  : process   = 1 : (0..1)     一个槽最多一个系统进程
instance : slot   = 1 : 1          一个实例只占一个槽（L2 冲突时可能被误配成多个实例同槽）
instance : lease  = 1 : (0..1)     没开过 = 无租约
lease    : session = 1 : (1..N)    同一进程里可以开多个窗口（task）
session  : task   = 1 : 1
```

**关键区分**：**租约是"占槽凭证"，不是"会话清单"**。同一 instance 连按"启动新实例"会开多个 task，它们**共享同一租约/同一进程**，只有系统 task 列表能数清会话数（`SlotStatus.tasks`）。

### 1.5 原有命名（代码为准，**不要另造符号**）

| 名称 | 现有格式 | 来源 |
| --- | --- | --- |
| 实例 ID | `mark.via_1795668196660`（`<包名>_<epochMillis>`） | `VirtualEngine.installApp` |
| 系统面进程名 | `com.nextvm.app:pN` | `app/src/main/AndroidManifest.xml`（stub 组件 `android:process=":pN"`） |
| 虚拟进程记录名（容器管理面） | `mark.via:p0`（`$packageName:p$processSlot`） | `VirtualActivityManagerService.startProcess`（`:134`） |
| slot 简称 | `:pN` / `pN` | `VirtualProcessManager`（`:p$index`）、UI 里的 `p0` |
| 命令/日志/文档的字段表述 | **`key=value` 空格分隔**（`pkg=` `instance=` `slot=` `pid=` `task=`） | `CmdReceiver` LIST：`APP pkg=… instance=… slot=… main=… apk=…`；`bind start instance=…` |
| stub 组件名 | `com.nextvm.app.stub.P<N>$Standard<NN>` | `StubRegistry` |

**本项目从未使用 `@`、`#` 这类符号做标识**（`mark.via@p0` / `mark.via#1` 是我在文档草稿里自造的，已废弃）。表述一律用上表的 `key=value` 或既有简称 `pN`。

---

## 二、guest 进程名：纯包名 vs 包名+编号

### Android 的规范

| 进程 | 规范名 |
| --- | --- |
| 主进程 | **= 包名**（`mark.via`） |
| 子进程 | `包名:后缀`（`mark.via:remote`） |

所以 **guest 视角用纯包名（`mark.via`）是规范做法**：真机上的 Via 主进程就叫 `mark.via`。

### 两层允许不同的名字（本轮结论）

| 层 | 进程名 | 允许"包名+编号"吗 |
| --- | --- | --- |
| **系统面**（AMS/PMS/AppOps） | `com.nextvm.app:pN` | 不适用（manifest 决定） |
| **容器管理面**（VAMS 记录、日志、ps 注释、调试 UI） | **允许** `mark.via:p0`（`$packageName:p$slot`） | ✅ 用户 2026-11-27 明确允许，便于区分实例/槽 |
| **guest 可见面**（`Application.getProcessName()`、`/proc/self/cmdline`、comm、`getRunningAppProcesses` 本进程条目） | **纯包名 `mark.via`** | ❌ 不用编号 |

**为什么 guest 面不能用编号**：

| 后果 | 依据 |
| --- | --- |
| SDK 会认为自己在**子进程** | 大量 SDK 用 `processName == packageName` 判主进程；容器自己的 `VirtualMultiProcessManager.isMainProcess` 也是这么判 |
| 与真机**不一致**（检测面） | 真机 Via 主进程不带 `:p0`；这本身就是"被多开"的特征 |
| 编号的收益在容器层已经拿到 | VAMS 记录/日志/slot 字段都能区分，guest 不需要知道 |

> 因此 `VirtualActivityManagerService` 的 `mark.via:p0` **保留**（它属于容器管理面），不算"待修偏差"；只要它不泄漏到 guest 可见 API 即可。

---

## 三、多 guest 多实例的表述规范

### 表述语法（与 `CmdReceiver LIST`、breadcrumb 一致：`key=value`）

```
guest=<guestPkg> instance=<instanceId> slot=<N> pid=<pid> task=<taskId> tasks=<N> state=<STATE>
```

| 场景 | 表述（key=value） | 系统面进程名 | guest 面进程名 |
| --- | --- | --- | --- |
| 1 个 via | `guest=mark.via instance=mark.via_1795668196660 slot=0` | `com.nextvm.app:p0` | `mark.via` |
| 再开一个探针 | `guest=com.sandbox.probe instance=com.sandbox.probe_1795668197000 slot=1` | `com.nextvm.app:p1` | `com.sandbox.probe` |
| 同一 via 开第二个窗口（长按 → New instance） | `guest=mark.via slot=0 tasks=2` | 仍是 `:p0`（**同进程**） | 仍是 `mark.via` |
| 同一 instance 的 N 个窗口 | `guest=mark.via slot=0 tasks=N taskIds=1723,1730` | `:p0` | `mark.via` |
| **真正的同包双实例**（两套独立数据） | `guest=mark.via slot=0 instance=mark.via_1795668196660` + `guest=mark.via slot=2 instance=mark.via_1795670000000` | `:p0`、`:p2` | 都是 `mark.via`（guest 面区分不了，也不需要） |

**人读短写**：`mark.via slot=0`（或 UI 里的 `p0` + 包名），**不要**写成 `mark.via@p0`。

### 当前能力的边界（重要）

| 想要 | 现在能不能 | 原因 |
| --- | --- | --- |
| 1 个 via + 1 个 probe 同时跑 | ✅ | 两个 instance、两个 slot |
| 同 1 个 via 开多个窗口 | ✅ | `forceNewSession`（新 stub → 新 task，同进程） |
| 同 1 个 via 开**两套独立数据**（真多实例） | ❌ | `VirtualPackageManagerService.registerPackage` 用 `packages[app.packageName] = record`（`:81/:163`）→ 同包第二实例**覆盖**第一个；多实例 2026-11-25 起暂停 |
| 两个 probe 各开一个窗口 | ✅ | 同上（同一 instance 多 task） |

**所以要表达"我启动多个 via"时，先区分愿望**：
- **多个窗口** → `guest=mark.via slot=0 tasks=N`；
- **多套数据**（多开）→ 需恢复多实例（VPMS 改成以 `instanceId` 为键 + 合成路径带 instanceId），属独立立项。

---

## 四、UI 上怎么显示（与 `ProcessSlotsScreen` 对齐）

```
slot=0  mark.via          RUNNING  pid=20511 task=1723 tasks=2 com.nextvm.app.stub.P0$Standard00
   ├─ task=1723 acts=1 P0$Standard00   [Close]
   └─ task=1730 acts=1 P0$Standard01   [Close]
slot=1  com.sandbox.probe RUNNING  pid=20512 task=1740 tasks=1
```

- 一行 = 一个 **slot**（不是 instance、不是 task）；
- `tasks=N` = 该 slot 的**会话数**；
- 每行可展开到 **task**，清理粒度 = task（用户要求：不能一下子把同类 guest 全关）；
- 现有 UI 的 `p0` 是 `:p0` 的既有简称，可保留；**不要**新增 `@`/`#`。

---

## 五、结论（2026-11-27 拍板）

| # | 结论 |
| --- | --- |
| 1 | **guest 可见面进程名 = 纯包名**（`mark.via`）——规范、与真机一致、不会被 SDK 判成子进程 |
| 2 | **容器管理面允许 `包名:编号`**（`mark.via:p0`，即 `$packageName:p$slot`）——用于 VAMS 记录/日志/调试，**但不得泄漏到 guest 可见 API** |
| 3 | **系统面保持宿主**（`com.nextvm.app:pN`）——这是 AMS/PMS/AppOps 的身份，改了会被系统拒 |
| 4 | **表述一律 `key=value`**（`guest=` `instance=` `slot=` `pid=` `task=` `tasks=`），与 `CmdReceiver LIST`、breadcrumb 一致；**不引入 `@`/`#` 等符号**；`pN` 是 `:pN` 的既有简称 |
| 5 | **不加** `processNameWithSlot` 开关（用户要求不轻易引入非规范命名）；若将来需要，默认 0 且只影响容器管理层 |
| 6 | "启动新实例" = **新会话（新 task，同进程）**；真·多实例单独立项（VPMS 键改 instanceId） |

---
