# 计划：process slot 缺陷登记（2026-11-27，**暂不修复**）

- 用户 2026-11-27 实测后要求：**先记录，不修**；当前重点仍是重构（native / 臃肿代码）。
- 编号沿用启动链缺陷的 `L` 系列之外的新前缀 `S`（slot 会话级），避免与 L1–L11 冲突。

---

## S1：5 个窗口后无法指定打开哪个（UI 缺 task 选择）

| 项 | 内容 |
| --- | --- |
| 现象 | 同一 instance 用满 5 个 standard stub（`tasks=5`）后，列表只能看到行级汇总，**无法选择"打开第 N 个窗口"**；`Bring to front` 只会切到当前 task |
| 根因初判 | `SlotStatus.tasks` 已有 taskId 列表，但 UI 的 `Bring to front` 只调 `engine.bringToFront(instanceId)`（取 `taskId` 字段 = 第一个有 Activity 的 task）；**没有按 taskId 切前台**的入口 |
| 候选修法 | ① `SlotStatus` 行内每个 task 加"Open"按钮 → `engine.bringTaskToFrontById(taskId)`；② 或 `bringToFront(instanceId, taskId)` 重载 |
| 影响 | 多窗口场景无法选择；不影响单窗口 |

## S2：手动关闭 task 后**没有空出槽位**（关了再开仍失败）

| 项 | 内容 |
| --- | --- |
| 现象 | 5 个 stub 用尽后，在列表里逐个 `Close` 掉 task，仍然报 `No available stub for process slot 0`，无法再开新窗口 |
| 根因初判 | `StubRegistry.inUseStubs` **只在 `stopApp`（SIGKILL 后 `releaseAllStubsForSlot`）归还**；`cleanupTask`/`cleanupSlot` 只 `finishAndRemoveTask()`，不归还 stub。且 `TaskRef` 没有 `stubClassName` 字段 → 无法把"关掉的 task"映射回"哪个 stub" |
| 候选修法 | ① `TaskRef` 增加 `stubClassName`（`SlotStatusProbe` 从 `topComponent/baseIntent` 已能解析）；② `cleanupTask(taskId)` 成功后 `stubRegistry.releaseStub(stub)`；③ 若该 slot 的 task 全关，可连租约一起释放 |
| 影响 | **直接卡死"关了再开"**（用户实测）→ 优先级高于 S1，但用户已定暂不修 |

## S3：`Process Slots` 页的 `Refresh` 按钮"暂无功能"

| 项 | 内容 |
| --- | --- |
| 现象 | 点 Refresh 后列表不变化（用户实测） |
| 根因初判 | ViewModel 的 `Intent.Refresh -> refresh()` 存在，且 `slotStatus(force = true)` 会绕过 250ms 缓存；可疑点：① 点击后 `uiState` 赋值相同（数据确实没变 → 用户感知"无功能"）；② 或 `LazyColumn` key 不变导致视觉无刷新；③ 或按钮被 Compose 重组吞掉 |
| 候选修法 | 加"刷新时间戳"到 UiState（`lastRefreshedAt`）显示在标题栏；或加 loading 指示（`isLoading=true` 一闪） |
| 影响 | 观感/可诊断性，不阻塞功能 |

---

## 与当前重点的关系

- 这三条都属于 **process slot 会话管理**，不在本轮"native / 臃肿代码重构"范围；
- **不修**的前提下可用：
  - 每个 slot 仍建议「一个 instance 一个窗口」（避免撞 S1/S2）；
  - 需要重开时用 `STOP`（会归还 stub）而不是逐个 `Close`。

## S4：`STOP`（SIGKILL）后系统侧 ActivityRecord 残留 `app=null`，阻塞同 affinity 后续启动

| 项 | 内容 |
| --- | --- |
| 现象 | 自动化脚本连续 `STOP → LAUNCH` 后，guest 进程**再也起不来**：`ps` 无 `mark.via`、`dumpsys activity activities` 里残留 `ActivityRecord{… .stub.P0$Standard01 t1759 … app=null}`；`Launched` 日志照样打印（`startActivity` 成功），但进程没 fork |
| 证据（2026-11-27） | ① T1/T2（framework）正常；② 切 legacy（`setprop frameworkBind 0`）→ STOP → LAUNCH 后无新 breadcrumb（进程没起来）；③ 恢复 framework=1 再 STOP+LAUNCH **仍失败**（说明与路径无关）；④ `am force-stop com.nextvm.app` + `am start` 后**立即恢复**（`mark.via` pid 2070） |
| 根因初判 | `stopApp` 只做「逻辑终止 + SIGKILL」，**不 finish 系统 task**；AMS 保留 `app=null` 的 ActivityRecord，认为 Activity 仍等待进程 attach → 同 affinity 的后续 `startActivity` 被折叠/阻塞。与 S2（关 task 不释放槽）同源：**进程/task/槽三方状态不同步** |
| 影响 | 用户感知为「容器被关了 / 关了再开不行」；自动化脚本会自伤 |
| 候选修法 | ① `stopApp`/`cleanupSlot` 先 `finishAndRemoveTask` 再杀进程；② 或 `GuestProcessStopper` 杀进程前按 `activityStack` 逐个 `finishActivity`；③ 至少 `STOP` 后清理该 slot 的残留 task（需要 taskId ↔ slot 映射） |
| 暂不修 | 用户 2026-11-27：不在本轮重点；先把测试脚本改成不制造残留（见下） |

## 自动化冒烟现状（2026-11-27，`tools/device/smoke-process-slot.ps1`）

| 项 | 结果 |
| --- | --- |
| T0 容器就绪 / T0.1 实例存在 | PASS |
| T1 首次 LAUNCH + T1.1 stub 分配 | PASS（`P0$Standard00`） |
| T2 复用 + T2.1 pid 不变 | PASS（`Reused live session`，pid 28579 前后一致） |
| T3 SELF_CHECK | PASS（`hookcheck ok=8 fail=0`） |
| T4 主进程不被改名（T7） | PASS（`ProcTrace … name=com.nextvm.app`） |
| T5/T5.1/T6 legacy/framework 切换 | **FAIL，原因是 S4 的残留 task**，不是 B4-4f 回归（force-stop 后同版本立即恢复） |
| 结论 | B4-4f 搬迁在可执行的链路上**无回归**；`STOP` 循环必须配清场（force-stop，需许可） |

## 什么时候修

| 触发条件 | 建议 |
| --- | --- |
| 开始做多实例（M1/M2）之前 | 先修 S2/S4（否则多实例 + 池泄漏 + 残留 task 会互相放大） |
| 用户开始日常多窗口使用 | S1 → S2 → S4 → S3 顺序 |
