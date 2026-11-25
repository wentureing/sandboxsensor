# 变更：B4-1 拆 VirtualApplicationManager（诊断转储两个职责）

- 日期：2026-11-25
- 状态：**执行中**（分支 `split/b4-diag-dump`，待 `module-test :core:virtualization`）
- 依据：`docs/输出/调研/调研-高频修改热点与测试优先级.md`、`docs/计划/计划-大文件拆包与职责导出.md`
- 粒度：**两个职责**（用户 2026-11-25：Java/Kotlin 授权放松，但仍保持每批两个职责）

---

## 一、为什么拆这个文件

`VirtualApplicationManager.kt` = **4 589 行**（文档旧值 4 427 已过时），其中诊断/探针类函数约 **2 400 行（>50%）**。
读启动链时要在探针里翻——这是"排查故障不方便"的真正来源，不是行数本身。

本批先搬**纯转储**（无启动链控制流、无业务副作用），风险最低。

## 二、本批范围（两个职责）

| 职责 | 函数 | 行数 | 去向 |
| --- | --- | --- | --- |
| **① 线程栈转储** | `dumpGuestStacks(out, label)` | 61 | `lifecycle/diag/GuestMemoryDump.kt` |
| **② 内存 dex 转储**（含工具 `le32`） | `dumpMemoryDex(application)` + `le32(b, off)` | 205 + 6 | 同上 |

调用点只改两处：`VirtualApplicationManager:970`（watchdog 里）与 `:2584`（plugin probe 里）。

## 三、行为等价论据

| # | 面 | 判据 |
| --- | --- | --- |
| 1 | 代码**逐字搬运** | 脚本按行号区间抽取（977-1037 / 2757-2961 / 2963-2968），只改 `private fun` → `fun`（object 内可见性）与缩进 |
| 2 | `DiagSuspendGate` 串行化 | 原逻辑保留（`synchronized(DiagSuspendGate.lock)`）；新增 `import ...DiagSuspendGate` |
| 3 | 输出文件与格式 | `stackdump-<pid>.log` 内容格式不变（`==== label ====` + 主队列 + 线程栈）；`memdex-<pid>-<n>.dex` 命名与校验条件不变 |
| 4 | 调度时机 | **不动**：watchdog / plugin probe 仍在原处调用，只换成 `GuestMemoryDump.xxx(...)` |
| 5 | debug 门禁 | 本类不引入任何新日志；`GuestBreadcrumb` 调用原样 |

## 四、影响范围

| 文件 | 类型 | 变化 |
| --- | --- | --- |
| `core/virtualization/.../lifecycle/diag/GuestMemoryDump.kt` | **新增** | 290 行（object 内三个函数） |
| `core/virtualization/.../lifecycle/VirtualApplicationManager.kt` | 修改 | −272 行；2 个调用点改为 `GuestMemoryDump.xxx`；新增 1 个 import |
| `core/virtualization/src/test/.../diag/GuestMemoryDumpTest.kt` | **新增** | G1–G5（`le32`，纯 JVM） |
| `docs/代码镜像/core/virtualization/diag/GuestMemoryDump.md` | **新增** | 规则 7 四节（含 `DiagSuspendGate` 的坑） |

## 五、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 搬漏 import（如 `DiagSuspendGate`） | `check-imports` 立刻报出（本次已命中并修）；CI 编译兜底 |
| 2 | 反射字段在 Android 版本变化时失效 | 原逻辑已 try/catch 降级；行为未改 |
| 3 | 转储内容变化 | 逐字搬运 + 镜像文档记录输出格式；真机 smoke 对比 `stackdump-*` 首行 |
| 4 | 与后续批次（`dumpRealMaps`）冲突 | `dumpRealMaps` 依赖 `RealSnap`，本批不动它 |

## 六、回退

```powershell
git -C _ref/ShitJumpingVM checkout baseline-v150
git -C _ref/ShitJumpingVM branch -D split/b4-diag-dump
```

## 七、验证

| 项 | 判据 |
| --- | --- |
| 静态 | `kotlin-lint`（23 文件）+ `check-imports`（仅 1 处既有误报） |
| 单测 | `module-test :core:virtualization`（含 G1–G5 + 既有 20 例） |
| 真机 | 装包后 `stackdump-<pid>.log` 仍生成、首行 `==== <label> ====`；`memdex-*` 行为不变 |
| 行数 | `VirtualApplicationManager` 4 589 → 约 4 317 |

## 八、debug 数据保障边界（每批拆分的准入条件）

> 用户 2026-11-25：**"搬走后，启动链的 debug 数据谁来保障？"**
> 答案不是"文件在哪"，而是下面三条。**不满足就不许合。**

| # | 硬约束 | 要避免的反面 |
| --- | --- | --- |
| 1 | **只搬实现，不搬触发** | 把"注册生命周期回调 / 启动定时器 / 决定何时 dump"一起搬进新包 → 启动链失去触发点，数据静默消失 |
| 2 | **上下文靠参数传入** | 新包反向读 `hostApp`/`instanceId`/启动链私有字段 → 依赖倒挂 |
| 3 | **不新增日志格式** | 借搬家"顺手优化日志" → 现有排查 grep（`v1xx-*`、`BootCrumb`）失效 |

具体分工：

| 留在启动链（装配/触发） | 搬到新包（实现） |
| --- | --- |
| `bindApplication`、`registerBreadcrumbCallbacks`（144 行，是"装配"不是探针） | `probePluginLoaderState` 等探针**函数体** |
| `startWatchdog` 的调度、`scheduleGuestStackDumps` 的定时与 label | `dumpRealMaps` / `parseRealMaps` 等转储**函数体** |

每批拆完必跑 3 项（能力清单全量回归留到最后）：

```
1) 探针 diag 套件 → D01–D05 全 ok
2) stackdump-<pid>.log 首 3 行
3) breadcrumb 尾部：bind complete → onActivityResumed
```

## 九、测试设计：诊断代码怎么测

> 用户 2026-11-25：**"这里怎么设计测试？"**

诊断代码的困难是"依赖 Android 对象（Application/Activity/View/反射），纯 JVM 跑不了"。
解决办法不是 Robolectric（CI 不可用），而是**把"取数"和"判断/格式化"分开**：

```
Android 取值（不可测）  →  纯函数（可测）  →  写出（可测，注入 sink）
   NativeHookBridge.readRealMaps()      parse(text): RealSnap        sink.log(行)
   activity.findViewById(...)            decide(inputs): Result       sink.log(行)
```

### 4 条法则

| # | 法则 | 落地 |
| --- | --- | --- |
| 1 | **取数与纯逻辑分离** | 搬家时把"解析/统计/判定/格式化"抽成 `internal fun`，签名只吃 `String`/基础类型/数据类 |
| 2 | **输出走 sink 注入** | 新包不直接调 `GuestBreadcrumb`，而是收一个 `sink: (String) -> Unit`（默认实现转发 `GuestBreadcrumb.log`，行为不变；测试传 `RecordingSink` 断言文本与顺序） |
| 3 | **golden 文本向量** | `parseRealMaps` 用真机 maps 片段（存 `src/test/resources/golden/`）作输入，断言统计值；格式类断言整段文本 |
| 4 | **不可测的部分显式豁免** | 带 `Activity`/`View` 的入口只能在真机验证 → 写进镜像文档「约束与坑」，并对应探针编号 / breadcrumb 检查项 |

### 每类代码的测试清单

| 代码类型 | 测试方式 | 例 |
| --- | --- | --- |
| 解析（maps / dex header / trace 行） | 纯 JVM + golden 文本，覆盖畸形输入 | `parseRealMaps`：正常/空/畸形/无 path |
| 差分计算（前后快照对比） | 纯 JVM：`prev=null` 与 `prev` 有值两个分支 | `formatLine`：`newExecRanges` / `newBigAnon` |
| 格式化（日志/转储文本） | 纯 JVM：断言整段字符串（字段顺序、`0x` 前缀） | `v59 realmaps ...` 行 |
| 文件写出（命名/内容/失败静默） | `TemporaryFolder`：断言文件名与内容；`dir` 不存在时不得抛 | `dump(filesDir, pid, tag, snap)` |
| Android 取值入口（`findViewById` 等） | **不写单测**：依赖真机 + 探针 + breadcrumb，在镜像文档「约束与坑」写明 | `probeLoginButtonState` |

### 门禁

- 规则 9：每搬一个文件 = 一个 `*Test.kt` + 一份镜像文档；
- 规则 10：搬完对 `_ref` 原实现跑 `check-refactor-equivalence.mjs`，证明逻辑未变；
- CI：`module-test :core:virtualization`（按包粒度可用 `test_filter`）。

## 十、后续批次（未做）

| 批 | 内容 | 说明 |
| --- | --- | --- |
| B4-2 | `dumpRealMaps`（378 行）+ `RealSnap` 相关 | 依赖快照类型，需一并搬 |
| B4-3 | plugin / login / autofill / auth-intent 探针（≈1 400 行） | 需参数化 `application`/`activity` |
| B4-4 | 启动链：`bindApplication` / `createApplication` / `initContentProviders` | 最高风险，先补 breadcrumb 契约测试 |

## 十一、实际结果（已合并 + 真机验证）

| 项 | 结果 |
| --- | --- |
| CI | `module-test :core:virtualization` **成功**（`45604572400`；期间修过一处测试断言：`le32` 读 4 字节，我最初按 2 字节写） |
| 合并 | `--no-ff` → `baseline-v150 @ 55f7a24` |
| 容器包 | `container-shitjumpingvm-c5ab1cb.apk`（`build-container` `45607135986` 成功，**发包已前置到单测之前**） |
| 安装 | 干净安装 `versionName=1.0.0-c5ab1cb` / `versionCode=111`；`MANAGE_EXTERNAL_STORAGE` 已恢复 |
| **真机验证** | 探针 round 跑完：`suite=round`、`entries=94`（ok 53 / fail 2 / skipped 39）；**`stackdump-5993.log` 生成 80 KB**，首行为 `==== t+0.3s ====` + `main looper queue:` + 反射字段 —— 与拆分前格式一致，证明 `GuestMemoryDump.dumpGuestStacks` 在容器内正常工作 |
| 未回归 | 2 个 fail 仍是 `J26/J27`（字面量宿主路径 + `javaIo=0`，已知限制） |

