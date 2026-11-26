# 变更：L1 stub 归还（Step 1）

- 日期：2026-11-27
- 状态：**已提交（`c21d56e`），待 CI + 真机**
- 上游计划：`20261127-process-slots-会话复用与UI解绑.md` §十（分步表）、§十一（Step 1 严格计划）、§3.2（副作用研究）
- 容器提交：`c21d56e`（`stopApp` + `StubRegistryTest` R1–R3 + 三通道插桩）

---

## 一、目标

`STOP` 之后同一 `processSlot` 可以反复 `LAUNCH`，不再出现 `No available stub for process slot N`
（根因：`StubRegistry.resolveStub` 把 stub 放进 `inUseStubs`，而生产代码从无 `release*` 调用点）。

**逐步实施的第 1 步**：只做"归还"，不改"每次点击分配新 stub"（那是 Step 2 会话复用）。
本步完成后 U1（点 guest 回到原进程）**仍不满足**，属计划内的中间态。

## 二、影响范围

| 文件 | 类型 | 改动 |
| --- | --- | --- |
| `core/virtualization/.../engine/VirtualEngine.kt` | 修改 | `stopApp`：在 `SIGKILL` 之后归还该 slot 的 stub（双守卫 + 可观测日志） |
| `core/virtualization/src/test/.../engine/StubRegistryTest.kt` | 修改 | 新增 R1–R3 |
| `docs/输出/变更/20261127-l1-stub-release.md` | 新增 | 本文件 |
| `docs/计划/计划-启动链已知缺陷（重构后修复）.md` | 修改（待办） | L1 标记已修 + 修正过时行号 |
| `docs/架构/架构-启动链契约.md` §六 #1、`docs/现状/零调用扫描报告.md:228`、`STATUS.md` §2 | 修改（待办） | 同批更新（规则 6） |

## 三、diff 预览（实际实现）

```diff
 // VirtualEngine.kt  stopApp(...)
         val pkg = findApp(instanceId)?.packageName ?: instanceId.substringBefore('_')
         val kill = runCatching { GuestProcessStopper.stop(pkg) }
             .getOrElse { GuestProcessStopper.StopResult(emptyList(), 0) }
         Timber.tag(TAG).i("Stopped app: $instanceId (pids=${kill.pids} killed=${kill.killed})")
+
+        // L1：stub 池按 slot 归还，否则每 slot 只够 5 次启动（`StubRegistry.resolveStub` 只借不还）。
+        // 位置必须在 SIGKILL 之后：VAMS 记录已清、进程已死。
+        // 守卫 1：同 slot 还有别的实例在跑就不归还（L2 未修时两实例共用 slot）。
+        // 守卫 2：容器重启后 stubRegistry 可能尚未初始化。
+        if (slot != null && ::stubRegistry.isInitialized && runningApps.values.none { it == slot }) {
+            stubRegistry.releaseAllStubsForSlot(slot)
+            // 三条通道：checks.log（结构化，探针读）/ debug-trace.log（Timber）/ logcat（兜底）
+            DiagnosticsHolder.stage(
+                "slot",
+                "released stubs slot=$slot killed=${kill.killed}/${kill.pids.size}",
+            )
+            DebugTrace.event(
+                "l1",
+                "released stubs slot=$slot killed=${kill.killed}/${kill.pids.size}",
+            )
+            Timber.tag(TAG).i("Released stubs for slot $slot (killed=${kill.killed}/${kill.pids.size})")
+        }
         return VmResult.Success(Unit)
```

**三个设计要点（都有代码依据）**：

| 要点 | 为什么 |
| --- | --- |
| 放在 `SIGKILL` 之后（`:760-765` 之后） | 放在 `processManager.releaseSlot` 旁（`:739`）会**编译不过**（`kill` 尚未定义），语义也变成"杀进程前归还 stub" |
| `runningApps.values.none { it == slot }` | `stopApp` 首行已 `remove` 本实例；L2 未修时另一实例可能同 slot，无此守卫会把**别人正在用的 stub 名**还回池子（副作用研究 §3.2 #2） |
| 日志带 `killed/x` | 进程没杀干净（`killed < pids.size`）时立即可见——归还是否安全的唯一外部线索（§3.2 #1） |

## 四、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 归还的 stub 名仍被未真正结束的 Activity 使用 | 释放发生在 `killProcess`（清 VAMS `activityStack`）+ `SIGKILL` 之后；真机「STOP→LAUNCH ×6」+ 交替实例验证 |
| 2 | L2 现场（两实例同 slot）下误归还 | 守卫 1；真机判据"STOP via 时不出现 Release 日志" |
| 3 | `Standard00` 与 guest 内部固定用名重叠（L11） | **不在本步修**，只登记；本步让它从"每次点击"降到"每会话一次" |
| 4 | `stubRegistry` 未初始化 | 守卫 2（与 `:241`/`:318` 的既有写法一致） |
| 5 | 归还后 `StubRegistry` 的 `inUseStubs` 非线程安全 | 现状即如此（`launch`/`stop` 都在主线程）；登记在镜像文档「约束与坑」 |

## 五、回退方案

```powershell
git -C _ref/ShitJumpingVM revert <Step 1 commit>
```

行为回退到"只借不还"（每 slot 5 次上限），无其它连带影响。

## 六、验证方法

### 6.1 静态 + 单测

```powershell
node tools/lint/kotlin-lint.mjs _ref/ShitJumpingVM/core/virtualization/src
node tools/lint/check-imports.mjs _ref/ShitJumpingVM/core/virtualization/src
node tools/lint/check-test-fn-names.mjs _ref/ShitJumpingVM/core/virtualization/src/test/java/com/nextvm/core/virtualization/engine/StubRegistryTest.kt

node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:virtualization" test_filter="com.nextvm.core.virtualization.engine.*"
```

单测判据：R1（耗尽后归还可再解析）、R2（只归还该 slot）、R3（未分配过的 slot 归还是 no-op）+ 既有 G1–G10 不回归。

### 6.2 真机（判定只读文件 / 系统状态）

| 用例 | 步骤 | 判据 |
| --- | --- | --- |
| **U2-a 不耗尽** | 同一 guest `STOP` → `LAUNCH` **连续 6 次** | 每次都出现新的 `bind start`（breadcrumb）；**不出现** `No available stub` |
| **守卫** | `LAUNCH via` → `LAUNCH probe` → `STOP via` | 不出现 `Released stubs for slot 1`（probe 仍占 slot）；`STOP probe` 后才出现 |
| **不破坏既有** | `clean-install-and-test.ps1` | `SELF_CHECK ok=8 fail=0`；探针 `entries=98 fail=2(J26,J27)`；via `bind complete` + `onActivityResumed` |

辅助命令：

```powershell
adb shell run-as com.nextvm.app ls files/            # breadcrumb-*.log / checks.log
adb shell run-as com.nextvm.app cat files/checks.log | Select-String "hookcheck"
```

## 七、本步不做

- 不改 `launchApp` 的 stub 分配（Step 2 会话复用）；
- 不动租约表接线（Step 2/3）；
- 不修 L2（slot 分配算法）、L11（`Standard00` 重叠）。
