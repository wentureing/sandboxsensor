# 设计：process slot 的测试分层与 CI matrix 划分（2026-11-27）

- 回答：这轮修复（计数 / ORPHAN / 复用 / 归还 / 日志优先级 / 延迟）**该有几层测试**、**分进几个 matrix job**
- 关联：`AGENTS.md` 规则 9/10/18、`docs/输出/变更/20261127-process-slots-会话复用与UI解绑.md`、
  `.github/workflows/module-test.yml`

---

## 一、先把"被测行为"拆开，再谈层次

| # | 行为 | 代码位置 | 能否纯 JVM 测 |
| --- | --- | --- | --- |
| B1 | slot 五态判定（含 ORPHAN / CONFLICT / STALE） | `SlotReconciler` | ✅ 纯函数 |
| B2 | 每个 slot 的残留 task 数（"5 次点击=1 孤儿+5 task"） | `SlotReconciler`（`taskCount`） | ✅ |
| B3 | 租约语义（幂等 acquire / 双向查 / 回填 taskId / 释放） | `SlotLeaseRegistry` | ✅ 纯 Kotlin |
| B4 | **要不要复用会话**（切前台 / 沿用 stub / 新会话 / 丢租约） | `SessionReusePolicy` | ✅ 纯函数 |
| B5 | **占用计数口径**（RUNNING+ORPHAN，按 slot 去重） | `SlotUsage` | ✅ 纯函数 |
| B6 | stub 归还（只在 SIGKILL 后、同 slot 无他实例时） | `VirtualEngine.stopApp` + `StubRegistry` | ⚠️ 归还动作不可测；**池语义**可测（G/R 系列） |
| B7 | 启动失败要 E 级 / 落盘 | `VirtualEngine.launchApp` + `CmdReceiver` | ❌ Android（ActivityManager/Log） |
| B8 | 切前台（`AppTask.moveToFront`） | `VirtualEngine.bringTaskToFront` | ❌ 需真实 AMS |
| B9 | `slotStatus` 250ms TTL 缓存 | `VirtualEngine` | ❌ 时间敏感 |
| B10 | 点击不阻塞主线程（IO 调度） | `LauncherViewModel` | ❌ 需协程测试 + Android |
| B11 | 错误提示优先于 "Launched xxx" | `LauncherScreen`（Compose effect） | ❌ 需 Compose UI test |

**结论**：B1–B5（这轮新增/修改的核心语义）**全部可以纯 JVM 测**；B6 的一半可测；B7–B11 不可行或性价比低 → 用真机判据。

---

## 二、四个层次（每层回答不同问题）

| 层 | 跑什么 | 在哪跑 | 回答的问题 | 本次覆盖 |
| --- | --- | --- | --- | --- |
| **L0 静态契约** | `kotlin-lint` / `check-imports`（含嵌套类）/ `check-duplicate-imports` / `check-test-fn-names` / `check-diag-channels` / `gen-breadcrumb-golden --check` | 本地毫秒级 + CI（`module-test` 的 `:core:common` job 里跑 diag 契约） | "代码结构/日志通道契约还成立吗" | ✅ 本轮全过 |
| **L1 纯逻辑单测** | `SlotReconcilerTest` S1–S17、`SlotLeaseRegistryTest` L1–L9、`SessionReusePolicyTest` R1–R8、`SlotUsageTest` U1–U5、`StubRegistryTest` G1–G10+R1–R3 | `module-test` → `:core:virtualization` job（普通 JUnit，无 Android 依赖） | "判定/租约/复用/计数/池语义对不对" | ✅ **本轮新增 R/U/S15–S17/L8–L9** |
| **L2 契约（golden）** | `BreadcrumbGoldenContractTest` C1–C12 × 8 样本 | 同一个 `:core:virtualization` job（资源在 test/resources） | "guest 启动链顺序有没有被这轮改动破坏" | ✅ 本轮没动 bind 链，**必须保持绿** |
| **L3 设备语义（真机）** | U1–U5 + 能力清单 5 项；`ps -A` / `dumpsys activity activities` / `checks.log` | 设备（手动或脚本） | "切前台真的复用了吗 / 错误真的立即出现吗 / 计数与列表一致吗" | ⏳ 等新产物 |

> 没有 L4"引擎集成测试（Robolectric）"：见 §四。

---

## 三、CI matrix：**不新增 job**，靠"逻辑下沉"复用现有 job

### 现状（`module-test.yml`）

```
matrix.module = [":core:common", ":core:virtualization", ":core:hook", ":core:sandbox"]
+ cpp-core-test（独立 job）
```

### 本轮改动落点 vs matrix 覆盖

| 改动模块 | 是否被现有 matrix 覆盖 | 处理 |
| --- | --- | --- |
| `core:virtualization`（process/*、engine） | ✅ `:core:virtualization` job | 新增测试全放这里 |
| `core:common`（`DebugFlags.SESSION_REUSE` 常量） | ✅ `:core:common` job | 常量不需测试（`isOn` 已有覆盖） |
| `feature:settings`（VM 绑定） | ❌ 无 job | **只做绑定**，逻辑已下沉到 `SlotUsage`（core） |
| `feature:launcher`（VM/UI） | ❌ 无 job | 同上；UI 优先级靠真机判据 |
| `app`（CmdReceiver 分级日志） | ❌ 无 job | 不可测，真机 `checks.log` 验证 |

### 为什么**不加** `:feature:settings` / `:feature:launcher` job

1. **它们能测的只有"绑定"**：`usedSlots = SlotUsage.countInUse(slots)`、`withContext(IO) { engine.launchApp() }` ——
   真正的判据已经在纯函数里测过；再测一遍等于测 Kotlin 语法。
2. **成本**：两个 job 各要 Robolectric / Compose UI test 依赖、新 test source set、Robolectric 缓存；
   matrix 从 5 → 7，墙钟和维护面都涨（用户 2026-11-25 刚要求过"并行、不连坐、别让墙钟叠加"）。
3. **Shadow 测不到关键行为**：`ShadowActivityManager` **不实现 `getAppTasks()`**，
   `AppTask.moveToFront()` 也无 shadow —— B8/B9 仍然测不到，只能拿"没有价值的形式测试"。
4. **真机判据更便宜**：U1–U5 用 `ps -A` + `dumpsys activity activities` 就能给出 pid/taskId 级判据。

### 何时才该加新 job（判据，写下来避免以后凭感觉）

| 触发条件（任一） | 动作 |
| --- | --- |
| VM/UI 层出现**状态机**（≥3 个状态转移，如多步安装向导） | 加 `:feature:xxx` job + Robolectric |
| VM 逻辑 > 200 行 且无法下沉到 core | 同上 |
| 需要 Compose 语义测试（无障碍/布局断言） | 加 `:app` connected test（真机/模拟器，不进 module-test） |

### `test_filter` 用法

| 场景 | filter |
| --- | --- |
| 日常改 process 包 | `com.nextvm.core.virtualization.process.*` |
| 改 engine（含 stub 池） | `com.nextvm.core.virtualization.engine.*` |
| 本轮完整验证（含契约） | `com.nextvm.core.virtualization.*` |

---

## 四、每层红了怎么判（与契约文档一致）

| 层 | 先怀疑 | 处置 |
| --- | --- | --- |
| L0 | 括号/KDoc/import/通道名 | 直接修；`check-diag-channels` 红 = 改了日志文件名没走 TSV 三件套 |
| L1 | 断言固定的是"设计内行为"吗 | 按规则 18.4：编码错误修哪侧、行为偏移回退代码、应当变的改写断言并写变更文档 |
| L2 | 是不是 bind 链被动了 | 本轮未动 bind 链 → 红了先做回退验证（`git stash` 被测改动） |
| L3 | 是"未就绪"还是"真坏" | 先看 `ageMs`/时机（规则 18.5），再判回归 |

---

## 五、诚实清单：这轮**没有**测试覆盖的部分

| 项 | 为什么 | 替代判据 |
| --- | --- | --- |
| `bringToFront` 在 vivo/A15 是否被允许 | 需真实 AMS | U1：pid 不变 + 界面回到原页 |
| `slotStatus` 250ms TTL 的时序 | 时间敏感测试易 flaky | U1 连点：提示立即出现（无 4s 延迟） |
| `LauncherViewModel` 的 IO 调度 | 需 `kotlinx-coroutines-test` + 注入 dispatcher（会改构造函数） | 同上 |
| `CmdReceiver` 失败走 `Log.e` | Android Log | 真机 `checks.log` 的 `E slot` + `Log.e` 标签 |
| Snackbar 优先级 | Compose effect 调度 | 人工：第 6 次点击错误立即出现 |
| `SlotStatusProbe.getAppTasks()` 的 ROM 差异 | Shadow 不支持 | 列表页 pid/task 与 `dumpsys` 对照 |

> 这些不是"以后补"，而是**明确选择用 L3 真机判据代替**；只有出现"真机也测不稳"时，才升级到 Robolectric/Compose 测试（见 §三 判据）。

---

## 六、mock 边界：只用于测试，不进 release（2026-11-27 用户约束）

### 6.1 规则

| 规则 | 落地方式 |
| --- | --- |
| **测试替身只放 `src/test`** | 不在 `src/main` 写任何 `Fake*` / `Mock*` / `Stub*Double`；本轮 P1 甚至不需要替身（`LaunchPlanner` 纯函数 + `StubRegistry`/`SlotLeaseRegistry` 用真实现） |
| **生产代码只允许"接口 + 真实实现"** | 若将来引入 `TaskBringer` / `SlotStatusSource`（P2/P3），默认实现必须走真实 AMS；测试实现只放 test source set |
| **不新增 mock 库依赖** | `build.gradle` 不新增 `implementation`；确需时只允许 `testImplementation`，并在变更文档说明 |
| **release 不感知测试** | 本轮改动的开关（`debug.nextvm.sessionReuse`）走 `DebugFlags`（`release` 里 `BuildConfig.DEBUG=false` → 整段被 R8 删除） |

### 6.2 自检（提交前）

```powershell
# 主源码里不允许出现测试替身命名
Select-String -Path _ref/ShitJumpingVM/*/src/main/**/*.kt -Pattern '\b(Fake|Mock)\w*'   # 期望 0 命中
# 依赖只在 test 配置
Select-String -Path _ref/ShitJumpingVM/**/build.gradle.kts -Pattern 'mockito|mockk'      # 期望只在 testImplementation（或 0）
```

### 6.3 为什么 P1 不需要 mock 也能测

`LaunchPlanner` 的输入是 `SlotLease` / `SlotStatus` / `List<String>`（纯数据），输出是 `LaunchPlan`（纯数据）——
**"把 Android 挡在边界外"比"mock 掉 Android"更便宜**，也不会随 ROM/API 版本漂移。

---

## 七、本轮 CI 盲区（真实教训，2026-11-27）

**现象**：`container-build` 红，`module-test` 绿。

**根因**：`module-test` 的 matrix 只编 `:core:common/:core:virtualization/:core:hook/:core:sandbox`，
**不编 `:app` 与 `:feature:*`**。这次错误恰好是 `app` 模块的
`import com.nextvm.core.virtualization.engine.VmResult`（`VmResult` 实际在 `core:model`）——
`core` 模块编译不受影响，所以单测全绿，直到 `container-build` 才炸。

**修复**：
1. 代码：改成 `import com.nextvm.core.model.VmResult`；
2. 工具：`check-imports.mjs` 新增**第 3 类检查**（import 路径与真实包不一致），
   并要求**跨模块一起扫**（`core app feature`）才有类型表 —— 负例/正例已验证（见工具头注释）；
3. 流程：改 `app`/`feature` 时，本地必须跑跨模块 `check-imports`；仍然只有 `container-build` 能证明"全量编译通过"。

**要不要给 module-test 加 `:app` 快编 job？** 结论：**暂不加**。
理由：`container-build` 已经覆盖全量编译（3–5 分钟），而"改 app 的轮次"本身就要产物装机；
加 job 会让每次改 core 也白编一次 app。判据不变：**当 app/feature 改动频率高到"每次都要等 3 分钟"成为瓶颈时**再加。

---
