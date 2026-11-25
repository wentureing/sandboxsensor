# 设计：诊断输出 = 关键路径验证序列 + golden 基线

- 日期：2026-11-24
- 目的（用户明确）：**验证关键路径、验证行为、输出错误，全部为 golden 准备**
- 与前一份 `设计-诊断输出抽象层.md` 的关系：那份讲"怎么换通道"，
  这份讲"**输出什么内容才配当 golden**"。抽象层是手段，golden 是目的。

---

## 一、目的决定了输出形态

| 目的 | 要求 |
| --- | --- |
| **验证关键路径** | 每个关键步骤必须产生**一条可判定成功/失败/未走到**的记录 |
| **验证行为** | 每条记录必须带 `expected` vs `actual`（否则无法判定"行为变了"） |
| **输出错误** | 失败必须带 `reason`，且**不能被淹没在正常输出里** |
| **为 golden 准备** | 输出必须**稳定、有序、可逐行 diff**，且**不含时间戳/随机值** |

**关键推论**：输出不是"日志"，而是**一份可断言的清单**。
所以：
- ❌ 不能有 `System.currentTimeMillis()`、hashCode、临时路径
- ✅ 必须固定顺序（按关键路径的 STEP 编号）
- ✅ 每行字段顺序固定、可解析

---

## 二、核心产出：验证序列

### 2.1 三条关键路径 → 三组验证

| 路径 | 来源（上游编号） | 验证条数 |
| --- | --- | --- |
| **native 安装** | `NativeHookBridge.initNativeHooks` 的 Phase 1/2 | 4 |
| **Application 启动** | `VirtualApplicationManager.createApplication` 的 Step 1→6 | 7 |
| **Activity 启动** | `ActivityThreadHook.handleLaunchActivityItem` 的 STEP 0→5 | 8 |
| 路径解析 | L-Java / L-native / L-syscall 三层 | 3 |

### 2.2 每条验证的内容（以 Activity 启动为例）

| STEP | name | target | expected | actual 取值 |
| --- | --- | --- | --- | --- |
| 0 | `preRegisterToken` | `mActivities` | `record_present` | `record_present` / `record_missing` |
| 1 | `swapActivityName` | `ActivityInfo.name` | `<guest class>` | 实际类名 |
| 1.5 | `breadcrumbWitness` | `breadcrumb` | `recording` | `recording` / `off` |
| 2 | `replaceClassLoader` | `LoadedApk.mClassLoader` | `guest` | `guest` / `host` / `same` |
| 2.1 | `resourceProbe` | `Resources` | `guest_apk` | `guest_apk` / `host_apk` |
| 2.2 | `apkWatchdog` | `apk file` | `exists` | `exists` / `missing` |
| 2.5 | `bootApplication` | `Application` | `created` | `created` / `failed` |
| 3 | `pendingResourceInject` | `pending` | `registered` | `registered` / `skipped` |
| 4 | `pendingContextWrap` | `pending` | `registered` | `registered` / `skipped` |
| 5 | `instrumentationHook` | `Instrumentation` | `hooked` | `hooked` / `already` / `failed` |

**每条都有 expected/actual** → 天然可断言。

---

## 三、输出格式（golden 友好）

### 3.1 一行一条，字段固定顺序

```
# <seq> <path>.<step> <name> ok=<ok|fail|skip> target=<...> expected=<...> actual=<...> [ctx...] [reason=...]
```

**真样例**（Activity 启动路径）：

```
0001 activity.step0   preRegisterToken      ok=true  target=mActivities        expected=record_present actual=record_present ctx=token=0x1234
0002 activity.step1   swapActivityName      ok=true  target=ActivityInfo.name   expected=mark.via.MainActivity actual=mark.via.MainActivity
0003 activity.step1.5 breadcrumbWitness     ok=true  target=breadcrumb          expected=recording actual=recording
0004 activity.step2   replaceClassLoader    ok=true  target=LoadedApk.mClassLoader expected=guest actual=guest same=false ctx=pkg=mark.via
0005 activity.step2.1 resourceProbe         ok=true  target=Resources           expected=guest_apk actual=guest_apk
0006 activity.step2.2 apkWatchdog           ok=true  target=apk file            expected=exists actual=exists ctx=path=base.apk
0007 activity.step2.5 bootApplication       ok=true  target=Application         expected=created actual=created ctx=cls=ViaApplication
0008 activity.step3   pendingResourceInject ok=true target=pending             expected=registered actual=registered
0009 activity.step4   pendingContextWrap    ok=true  target=pending             expected=registered actual=registered
0010 activity.step5   instrumentationHook   ok=true  target=Instrumentation     expected=hooked actual=hooked
```

**设计要点**：
| 要求 | 做法 |
| --- | --- |
| 稳定 | 无时间戳、无 hashCode、无临时路径；路径统一相对化（`<ROOT>`） |
| 有序 | `seq` 递增 + `step` 编号按关键路径顺序 |
| 可 diff | 一行一结论，字段顺序固定 |
| 可统计 | `ok=true/false/skip` 可 grep 计数 |

### 3.2 三态语义（关键设计）

| 状态 | 含义 | golden 对比时的意义 |
| --- | --- | --- |
| `ok=true` | 验证通过 | 与基线一致 → 正常 |
| `ok=false` | 验证失败 | **必须带 reason**；基线里若有 true 而现在是 false → **回归** |
| `ok=skip` | **未走到**（前置条件不满足） | 重要：区分"没执行"和"执行失败" |

**为什么需要 `skip`**：
- 比如 `STEP 2.5` 若因 engine 未 READY 而跳过，`ok=false` 会误导为"bootApplication 失败"
- `skip` + `reason=engine_not_ready` 才准确

### 3.3 错误单独成段（不被淹没）

```
--- checks ---
0001 activity.step0 preRegisterToken ok=true ...
...
--- errors ---
E 0003 v142 register failed: SecurityException: ...
E 0007 activity.step2.5 bootApplication failed reason=app_oncreate_threw
```

**理由**：golden 对比时，`checks` 段逐行 diff；`errors` 段**必须为空**才是健康的。
两段分开后，"有没有错误"一眼可见。

---

## 四、golden 生成与对比流程

### 4.1 三条 golden 的来源（分工）

| golden | 来源 | 生成方式 |
| --- | --- | --- |
| **纯函数 golden**（已 2 套） | 当前代码 | CI 首次自动生成 + 人工 review |
| **Robolectric golden**（路径矩阵，已 1 套） | Robolectric | `golden-from-baseline.yml` 可从原项目生成 |
| **验证序列 golden**（本次设计） | **真机** | 真机跑一次 → 导出 checks 段 → 人工 review → 提交 |

### 4.2 真机 golden 的生成（设计）

```
1) 真机跑：debug-trace.log 里的 checks 段（或 --checks-only 导出）
2) 拉到本地：
   adb shell "run-as com.nextvm.app cat files/checks.log" > checks-<sha>.txt
3) 相对化（路径 → <ROOT>/<GUEST>），去掉 errors 段
4) 人工 review 每行 expected/actual 是否合理
5) 提交为 golden：docs/现状/golden-baseline/<date>-<sha>-checks.txt
6) 之后每次回归：拉新的 checks → 与基线逐行 diff
```

### 4.3 对比要报"首处差异 + 统计"

```
checks 对比: 基线 27 条 / 当前 27 条
  差异 2 处：
    L0007: 基线 ok=true actual=created  ->  当前 ok=false actual=failed reason=app_oncreate_threw
    L0012: 基线 ok=true actual=guest    ->  当前 ok=skip
  统计: ok=24 fail=2 skip=1（基线 ok=27）
```

**判据**：
- `fail` 数增加 → 回归
- `skip` 出现 → 该步骤没走到（可能是上游失败导致，需结合前面的 fail 看）
- `ok` 数减少但无 fail/skip → 说明**少了几条 check**（代码路径变了）

---

## 五、抽象层（为"换通道"和"测试"服务，不是目的）

接口最小化，业务只依赖它：

```kotlin
interface Diagnostics {
    fun check(r: CheckResult)                    // 验证（核心）
    fun error(tag: String, msg: String, cause: Throwable? = null)   // 错误
    fun stage(tag: String, msg: String)          // 阶段转移（低频）
}

data class CheckResult(
    val path: String,        // "activity" / "application" / "native"
    val step: String,        // "0" / "2.5" / "phase2"
    val name: String,
    val state: CheckState,   // OK / FAIL / SKIP
    val target: String,
    val expected: String,
    val actual: String,
    val context: Map<String, String> = emptyMap(),
    val reason: String? = null
)
```

**为什么抽象在这里仍然必要**：
1. **golden 需要稳定格式** → 格式逻辑集中在一处，不散落 60 个调用点
2. **测试需要假实现** → 注入 `RecordingSink`，断言"STEP 2.5 产生了一条 check"
3. **通道可换** → 文件 / EventLog / 直接 stdout（CI 测试用）

---

## 六、这套东西能同时满足三个目的

| 目的 | 由什么满足 |
| --- | --- |
| **验证关键路径** | 每条 STEP 一个 check；`skip` 能区分"没走到" |
| **验证行为** | 每行 expected vs actual；golden diff 报首处差异 |
| **输出错误** | 独立 `errors` 段；`fail` 必带 reason |
| **为 golden 准备** | 无时间戳 + 固定顺序 + 字段固定 → 直接可 diff |

---

## 七、落地顺序（仍不执行，仅排序）

| 阶段 | 内容 | 为什么这个顺序 |
| --- | --- | --- |
| **1** | `CheckResult` + `Diagnostics` 接口 + `FileSink`（落 `checks.log`，复用现有 DebugTrace 的写盘） | 先有容器，再有内容 |
| **2** | 给 **Activity 启动 STEP 0–5** 加 10 条 check | 最关键路径，且已有清晰编号 |
| **3** | 给 **Application 启动 Step 1–6** 加 7 条 check | AOSP 语义（Provider 早于 onCreate） |
| **4** | 给 **native Phase 1/2** 加 4 条 check | 决定路径重定向是否真生效 |
| **5** | 删掉 `handleMessage` 逐条 detail（性能） | 有了 check 就不需要过程日志 |
| **6** | 真机跑 → 生成首份 checks golden | 这就是"为 golden 准备"的交付物 |
| **7** | 对比工具 `compare-checks.mjs` | 回归时用 |

**阶段 2–4 共 21 条 check**，覆盖三条关键路径的全部关键步骤。
