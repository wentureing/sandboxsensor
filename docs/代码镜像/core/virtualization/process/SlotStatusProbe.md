# SlotStatusProbe

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/process/SlotStatusProbe.kt`
> 来源：2026-11-27 process slot 会话复用（Step 0a，纯新增）

## 职责

**只回答一个问题：系统侧现在有哪些 `:pN` 进程、哪些属于容器的 task。**

- 负责：把 `ActivityManager.getRunningAppProcesses()` 与 `getAppTasks()` 转成纯数据（`ProcSnapshot` / `TaskSnapshot`）；
  解析工作全部委托 `SlotReconciler`。
- **不负责**：判定状态（`SlotReconciler`）、清理（`VirtualEngine`）、在 guest 进程里做事。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `runningProcessSlots(context): List<ProcSnapshot>` | 系统进程名 `com.nextvm.app:pN` → slot + pid | 需 `context` 是**容器主进程**的（主进程零 hook，拿到的是真实数据）；异常 → 空表 |
| `appTasks(context): List<TaskSnapshot>` | 容器自己的 task → slot / instanceId / numActivities / topComponent | 只保留能解析出 slot 的任务；`baseIntent` extras 可能被系统裁剪，所以 slot 解析**不依赖** extras |
| `snapshot(context): Pair<List<ProcSnapshot>, List<TaskSnapshot>>` | 一次取齐 | 任一侧失败返回空表，不抛 |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 用 `getAppTasks()` 而不是自建 task 表 | AOSP 里 task 归 AMS，容器再建一份就是第二份真相 | 必要偏差（避免状态分叉） | 真机 U5：列表的 taskId 与 `dumpsys activity activities` 一致 |
| 顶层界面名来自系统 | 系统看到的是 **stub 组件**（hook 只在 guest 进程内换类） | 中性偏差 | `topComponent` 形如 `com.nextvm.app.stub.P1$Standard00`，UI 上应换显 guest 的包名/界面名 |
| 不读 `RunningTaskInfo.isVisible` | 该字段 API 级别与 ROM 支持不一致，会引入版本分支 | 中性偏差 | 目前 UI 不区分"是否可见"，只区分"是否还活着" |

## 约束与坑

- **只能在容器主进程调用**。guest 进程里的 `ActivityManager` 被 `ActivityManagerProxy` 改写过（`ActivityManagerProxy.kt:226` 处理 `getRunningAppProcesses`），在那里探测会拿到虚拟数据。
- `getAppTasks()` 返回的是**本包**的 task（`REORDER_TASKS` / `GET_TASKS` 已在 manifest 声明：`app/src/main/AndroidManifest.xml`）。vivo 上仍需真机首测（变更文档 §七 风险 #1/#2）。
- `instanceId` 来自 `baseIntent` 的 `_nextvm_instance_id`；若为空，调用方应退化到"按 stub 类名解析 slot → 反查 instance"（`SlotReconciler.parseSlotFromStubClass` + `VirtualEngine.getRunningApps()`）。
- 所有调用都包在 `runCatching` 里：探测失败**不许影响**启动流程或 UI 打开（最坏情况是列表为空）。
- 这个文件是**唯一**允许出现 `ActivityManager` 依赖的地方；不要把它挪进纯 Kotlin 文件，否则单测环境会 `not mocked`。
