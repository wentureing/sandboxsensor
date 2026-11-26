# 架构：recents 多任务卡片与 guest 会话收尾（2026-11-27）

- 目标：让每个 guest（会话）在"近期任务"里**像独立 app 一样显示一张卡**；用户划掉那张卡 → 对应 guest **正常停止**，且**不危害容器自身**。
- 关联：`架构-slot租约与进程术语.md`、`架构-进程名与包名身份模型.md`、变更文档 `20261127-process-slots-会话复用与UI解绑.md`
- 外部资料：见 §八

---

## 一、要解决的三个问题

| # | 问题 | 答案的形态 |
| --- | --- | --- |
| A | recents 卡片怎么显示成"guest 的名字/图标"，而不是容器？ | `Activity.setTaskDescription(...)` + 每会话独立 task（已有） |
| B | 怎么知道"这张卡被划掉了"？ | 主进程 `ActivityManager.getAppTasks()` 对比（权威）+ guest 进程 `onActivityDestroyed`（辅助）+ `Service.onTaskRemoved`（可选） |
| C | 划掉后怎么收尾才"不危害容器"？ | 先释放租约/stub/VAMS 记录 → 再按需只杀**该 slot 的 guest 进程**；**永远不碰主进程** |

---

## 二、现状（本项目已具备的部分）

| 能力 | 现状 | 位置 |
| --- | --- | --- |
| 每会话独立 Task | ✅ 每个 stub 组件有独立 `taskAffinity`（`com.nextvm.vt.p1.s00`…），`launchApp` 带 `FLAG_ACTIVITY_NEW_TASK` | `AndroidManifest.xml`、`VirtualEngine.launchApp` |
| 会话清单 | ✅ `SlotStatus.tasks: List<TaskRef>`（taskId / numActivities / topComponent） | `SlotReconciler`、`SlotStatus` |
| 按 task 关闭（不杀进程） | ✅ `cleanupTask(taskId)` → `AppTask.finishAndRemoveTask()` | `VirtualEngine` |
| 进程已死 → 会话回收 | ✅ `reconcileSlots()`（STALE → 释放租约/stub/记录） | `VirtualEngine` |
| 真停实例（杀进程） | ✅ `STOP` → `stopApp` → `GuestProcessStopper`（按 guest 包名 SIGKILL） | `CmdReceiver`/`VirtualEngine` |
| guest 进程名 = guest 包名 | ✅ `setArgV0` + prctl + `getRunningAppProcesses` 适配 | `VirtualEngine`、`ActivityManagerProxy` |

**缺的只有两块**：① recents 卡片的**名称/图标**；② 把"卡片被划掉"变成**即时**的会话收尾信号（现在依赖下一次 `slotStatus`/`reconcileSlots` 才发现）。

---

## 三、机制 A：让 recents 卡片"像 guest"

### A1. 卡片标题与图标

在 stub Activity 进入 guest 生命周期时（`ActivityThreadHook` 换类之后、`guest Activity.onCreate` 之前），调用：

```java
activity.setTaskDescription(new ActivityManager.TaskDescription(
        guestAppName,            // 卡片标题
        guestIconBitmap,         // 卡片图标（Bitmap，不是 Drawable）
        guestPrimaryColor));     // 可选
```

- 图标必须从 **guest APK** 取（`VirtualPackageManagerService` 已有 icon 加载），不能用容器图标；
- 必须在 `onCreate`/`onResume` 早期设置（晚设置会先闪容器卡片）；
- `TaskDescription` 是**每 Task 一份**，所以在"新会话"（新 stub → 新 task）里各设一次即可。

### A2. 确保卡片可见

- 每个 stub 的 `taskAffinity` 已独立 → 系统天然给不同 Task → recents 多卡；
- 不要给 stub 加 `android:excludeFromRecents="true"`；如需按实例隐藏，用 `AppTask.setExcludeFromRecents(false)` 显式打开；
- 若希望"同一 Activity 多窗口"（而不是靠不同 stub），可用 `documentLaunchMode` + `FLAG_ACTIVITY_NEW_DOCUMENT|MULTIPLE_TASK`；本项目**不需要**（我们靠不同 stub 组件达到同样效果，且能控制 slot）。

### A3. 已知 ROM 差异

- 部分 ROM（含国内定制）**忽略 `setTaskDescription` 的 icon**，只认 label（社区实测，见 §八）；
- 兜底：卡片至少显示 guest 名称（label 生效面更广）；必要时用 `ActivityManager.TaskDescription` 的旧构造器（Bitmap 版本）。

---

## 四、机制 B：怎么知道"这张卡被划掉了"

**没有可靠的 Activity 回调**：从 recents 划掉通常**不会**走 `onDestroy`（进程可能被直接杀），这是 Android 的既有行为（见 §八 讨论）。

所以要分层：

| 信号 | 可靠性 | 说明 |
| --- | --- | --- |
| **① 主进程 `getAppTasks()` 对比** | **权威**（本项目主进程零 hook，拿的是真数据） | `slotStatus()` 已能列出每个 slot 的 `tasks`；记录上次快照，发现某 `taskId` 消失 → 该会话结束 |
| ② guest 进程 `ActivityLifecycleCallbacks.onActivityDestroyed`（`isFinishing`） | 辅助 | 能拿到"正常 finish"，拿不到"被系统杀"；用于写 breadcrumb/加速 |
| ③ `Service.onTaskRemoved(intent)` | 可选 | 只有"该 task 由 Service 启动/绑定"时才回调；容器可在每个 slot 放一个 stub Service 常驻绑定，换取即时信号（成本：多一个常驻组件） |
| ④ 进程消失 | 兜底 | 现有 `reconcileSlots()` 的 STALE 分支（进程不在 → 回收） |

**推荐组合**：①（权威）+ ④（兜底），②/③ 作为可选加速。

**采样时机**（避免常驻轮询）：`launchApp` 前、设置页刷新、ProcessSlots 页刷新、`STOP` 后——与现有 `slotStatus` 的 TTL（250ms）一致。

---

## 五、机制 C：收尾动作（"不危害容器"的关键）

发现"task 消失"后，按**固定顺序**：

```
1) 释放租约：slotLeases.release(instanceId)          // 主进程内存，无风险
2) 归还 stub：仅当该 slot 无其它租约 → stubRegistry.releaseAllStubsForSlot(slot)
3) 清 VAMS 记录：activityManager.killProcess(instanceId)（清 activityStack/serviceRecords）
4) 判断是否杀进程：
   - 该 slot 还有其它 task（其它会话）→ 不杀
   - 该 slot 已无 task、且进程不是"刚被复用/正在 bind"→ 可选 SIGKILL（GuestProcessStopper，按 guest 包名）
5) 写诊断：DiagnosticsHolder.stage("slot", "task removed task=<id> slot=<n> → released")
```

**硬约束**：
- **绝不调用** `Process.killProcess(Process.myPid())` 或杀主进程；`GuestProcessStopper.stop(guestPkg)` 只杀 `/proc` 里 cmdline = guest 包名的进程（即 `:pN`），主进程名是 `com.nextvm.app` → **天然不会误杀**。
- 同一 slot 上"多会话共享进程"时，**不能因为关了一个 task 就杀进程**（否则另一个窗口会炸）→ 守卫 = `tasks.isEmpty()`。
- `:pN` 进程被杀 → 该 slot 的其它会话全部消失；这是"共享进程"的必然结果，UI 上要如实显示（列表里该 slot 变 STALE/ORPHAN）。

---

## 六、失败模式与对策

| 失败模式 | 后果 | 对策 |
| --- | --- | --- |
| ROM 在划卡时直接杀 `:pN` 进程 | 会话记录变 STALE | 现有 `reconcileSlots()` 自动回收（已验证） |
| 划卡后进程存活但 task 没了 | 列表显示 RUNNING + `no-task` | `launchApp` 走 `REUSE_STUB`（沿用原 stub 重开）；`cleanupSlot` 可主动清 |
| task 列表采样窗口（250ms TTL）内连划两张 | 少一次事件 | 下次采样会补齐（幂等：release 幂等、cleanupTask 幂等） |
| 误判"task 消失"（系统折叠/多窗口） | 会话被释放但进程还在 | 释放只影响租约/stub，不杀进程；下次 launch 重新分配（安全侧） |
| `setTaskDescription` icon 不生效 | 卡片显示容器图标 | 至少 label 是 guest 名；记录 ROM 差异 |

---

## 七、落地清单（分步，不改启动链语义）

| 步 | 内容 | 位置 | 可测性 |
| --- | --- | --- | --- |
| **R1** | stub Activity 设置 `TaskDescription`（guest label/icon） | `ActivityThreadHook` 换类后 + guest icon 来源（`VirtualPackageManagerService`） | 真机看 recents |
| **R2** | 主进程"task 消失 → 会话收尾"：`reconcileSlots()` 已有 STALE；补一条"task 消失但进程在"的分支（释放租约/stub，不杀进程） | `VirtualEngine.reconcileSlots` + `SlotReconciler`（纯函数可测） | 单测（Reconciler）+ 真机（划卡） |
| **R3** | 可选：每 slot 常驻 stub Service + `onTaskRemoved` 即时信号 | 新文件（如 `lifecycle/TaskRemovalWatcher.kt`） | 真机 |
| **R4** | UI：recents 卡片名/图标 + ProcessSlots 页"task 消失"提示 | `ProcessSlotsScreen`、`ActivityThreadHook` | 真机 |

**验收（真机）**：
1. 同时开 via（一个窗口）+ probe（一个窗口）→ recents 出现**两张卡**，标题是 guest 名、图标是 guest 图标（ROM 支持时）；
2. 划掉 via 的卡 → via 的 slot 从列表消失/变 STALE；**probe 与容器都不受影响**；
3. 同一 via 开两个窗口 → 划掉其中一个 → 另一个仍在（列表仍显示该 slot，`tasks=1`）；
4. `checks.log` 出现 `S slot task removed …`。

---

## 八、参考资料

- `ActivityManager.TaskDescription`（label/icon/primaryColor）：<https://developer.android.google.cn/reference/kotlin/android/app/ActivityManager.TaskDescription>
- `FLAG_ACTIVITY_NEW_DOCUMENT`（多文档多 task 的基础）：<https://pub.dev/documentation/android_native/latest/content_Intent/Intent/FLAG_ACTIVITY_NEW_DOCUMENT-constant.html>
- recents 划卡与 `onDestroy` 不保证触发、`onTaskRemoved` 语义（社区实测/讨论）：
  - <https://www.volcengine.com/article/2932526>
  - <https://www.volcengine.com/article/2005074>
  - <https://mefmobile.org/why-androids-ondestroy-isnt-called-when-you-swipe-an-app-away/>
- `setTaskDescription` 图标在部分 ROM 不生效的实测：<https://www.volcengine.com/article/1306289>
- 同类开源项目（**实现路线不同**，仅作参考：它们用"真实 APK 安装 + 独立 UID"，我们是"App 级容器 + stub 进程"）：
  - <https://github.com/chuccp/nestspace>
  - <https://github.com/txorav/CloniX>
- 本项目内部：`架构-slot租约与进程术语.md`（slot/租约/进程定义）、`架构-进程名与包名身份模型.md`（进程名与包名分层）

---
