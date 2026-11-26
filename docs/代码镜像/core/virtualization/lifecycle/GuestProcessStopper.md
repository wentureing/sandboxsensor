# GuestProcessStopper（core/virtualization/lifecycle/GuestProcessStopper.kt）

## 职责

**负责**：真正**杀掉** guest 进程（SIGKILL），补齐「VM 层只改状态、进程还在」的缺口。

**不负责**：逻辑终止（`terminateApplication` 的 provider.shutdown / `onTerminate`）、实例数据清理（`uninstallApp`）、规则与 classloader 释放（`VirtualEngine.stopApp` 已做）。

## 函数

| 签名 | 作用 | 前提 |
| --- | --- | --- |
| `findPids(entries, pkg, uid): List<Int>` | 纯逻辑：从 `/proc` 快照里挑出 `pkg` 或 `pkg:xxx` 且同 uid 的 pid | 空 `pkg` 返回空 |
| `readProcEntries(): List<ProcEntry>` | 读 `/proc/<pid>/cmdline` + `status` 的 Uid（跳过自身） | Android 环境 |
| `stop(pkg): StopResult` | 找出并 SIGKILL | 调用前应先做逻辑终止 |
| `StopResult(pids, killed)` | 结果；`ok = 非空且全杀` | — |

## 偏差登记

| 偏差 | 为什么 | 类型 | 观测 |
| --- | --- | --- | --- |
| 用 `Process.sendSignal(pid, 9)` 而不是 `ams.forceStopPackage` | 容器内没有真实 AMS 权限；guest 与容器**同 uid**，同 uid 之间 `kill` 合法 | 必要偏差 | `LOG: Stopped app: <id> (pids=[...] killed=N)` |
| 只杀 `pkg` / `pkg:xxx`，不同 uid 一律不碰 | 防误杀宿主/系统进程；跨 uid 本来也无权限 | 必要偏差 | `GuestProcessStopperTest` S1–S4 |
| 不 `waitFor` 进程退出 | 杀掉后由下一次 LAUNCH 重新 bind；等待会拖慢命令通道 | 中性偏差 | 立即返回 `killed=N` |

## 约束与坑

1. **顺序**：先 `terminateApplication`（优雅清理），再 `stop`（SIGKILL）。反过来拿不到 `onTerminate`。
2. **必须用同 uid**：`Process.myUid()` 与 guest 相同（容器同进程 uid），这是 `kill` 能生效的前提。
3. **`/proc/<pid>/cmdline` 是 NUL 分隔**：读出来要 `substringBefore('\u0000')`，否则匹配永远失败。
4. **`killProcess(instanceId)`（`VirtualActivityManagerService`）只改记录**——不要再把它当"停进程"，见 `docs/现状/现状-容器自测覆盖矩阵.md` 类似缺口记录。
