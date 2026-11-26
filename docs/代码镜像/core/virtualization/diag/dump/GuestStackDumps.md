# GuestStackDumps

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/diag/dump/GuestStackDumps.kt`
> 来源：B4-4d 从 `VirtualApplicationManager.scheduleGuestStackDumps` 原样搬出（2026-11-26）

## 职责

**只回答一个问题：bind 之后什么时候、按什么节奏把 guest 的全线程栈转储下来。**

- 负责：起一个 daemon 线程，按 [marksFor] 的节奏调用 `GuestMemoryDump.dumpGuestStacks`，
  写 `stackdump-<pid>.log`；每进程只跑一次。
- **不负责**：怎么抓栈（`GuestMemoryDump`）、watchdog（`startWatchdog`）、其它诊断产物
  （`HijackTrace` / `InitTrace` / `MapsSnapshot`）。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `marksFor(pkg: String?): List<Pair<Long, String>>` | 采样节奏表：基础 7 点；联通 guest 追加 `+40s/+80s/+160s` | **纯函数**，无副作用 |
| `schedule(pkg: String)` | 起 daemon 线程按节奏转储 | 每进程一次（`scheduled` 门闩）；要求 `Class.forName("android.app.ActivityThread")` 能拿到 `currentApplication()`；失败全吞 |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 节奏表抽成 `marksFor()` | 原为函数内 `val baseMarks = listOf(...)` + `if (stackDumpPackageName == "…")` 分支 | **必要偏差**（可测性），语义等价 | `GuestStackDumpsTest` G1–G4 固定条数/递增/null；`schedule` 其余部分逐字等价（386 字符） |
| 两个状态字段从 VAM 搬进 object | `stackDumpsScheduled` / `stackDumpPackageName` → `scheduled` / `packageName` | 中性偏差（`VirtualApplicationManager` 是 `@Singleton`，object 同样每进程一份） | 每进程只转储一次；`stackdump-<pid>.log` 每次启动一个 |

## 约束与坑

- **每进程一次**：多 guest 复用同一进程时，只有第一次 bind 会转储（这是既有行为，不是缺陷）——排查"第二个 guest 没有 stackdump"时不要误判。
- **节奏表不能随意加长**：`+0.3s` 到 `+3s` 那一段是定点采样（v29 定位 init 停滞窗口），改动会让"停滞时刻"的栈取不到；
  联通 guest 的 `+40/80/160s`（v68）是为了回答"setup 慢但有界还是永不返回"。
- 探针 `D03` 的 `not-yet`/`missing` 三态直接依赖这个文件是否存在 → 报告时刻早于 `+0.3s` 时必然是 `not-yet`（属正常）。
- 线程里**任何异常都必须吞掉**（诊断不能干扰 guest）；`return@Thread` 用于 `currentApplication()==null` 的早退。
- 文件必须落在 **guest** 的 `filesDir`（`currentApplication().filesDir` 在容器里已重定向），否则 `run-as` 取不到。
