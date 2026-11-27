# 册：guest 启动链与 bind（2026-11-27）

- **时效**：2026-11-27 复核。触发复核：① 启动链拆分（B4-4 各批）② `frameworkBind` 默认值变更 ③ golden 重采
- **可信度**：A=实测（golden/日志）/ B=代码级
- **替代**：集中 `架构/架构-启动链契约.md`、`架构/架构-两条bind路径的由来.md`、`架构/架构-就绪依赖与早期窗口.md`、
  `设计/设计-启动编排测法与标准行为来源.md`、`计划/计划-启动链已知缺陷（重构后修复）.md` 的结论；原文保留
- 一句话：两条 bind 路径（framework / legacy）共用 C1–C3/C5–C6/C9–C11，C4/C7/C12 按路径分支；**golden 8 份 × C1–C12 是契约**

---

## 一、现行结论

| # | 结论 | 等级 | 证据 |
| --- | --- | --- | --- |
| **S-a** | 启动链顺序契约 **C1–C12**，由 8 份 golden（via/probe × framework/legacy × 2）驱动 | A | `BreadcrumbGoldenContractTest` |
| **S-b** | 同一路径的两次启动归一化后**逐行相等**（C11）；否则样本无效（早期窗口/进程复用） | A | golden 生成脚本守卫 |
| **S-c** | `frameworkBind` 默认 = framework 路径；`setprop debug.nextvm.frameworkBind 0` 才走 legacy | A | 采集脚本 |
| **S-d** | 两条路径都会先 `createFrameworkLoadedApk call/result`，legacy 只是**不交给 VAM** | A | 8 份 golden 逐行 diff |
| **S-e** | **provider 失败必须降级**（`provider FAILED (bind continues)`）：via 的 `androidx.startup` 就是这条 | A | golden C5 |
| **S-f** | 容器启动后**必须等 ≥10s**（早期窗口：虚拟 PM 包列表未就绪，`J19/J20` 会 `NameNotFound`） | A | `架构-就绪依赖与早期窗口.md` |
| **S-g** | 判定只读 `run-as` 下的文件（`checks.log`/`breadcrumb-<pid>.log`），**不要用 logcat**（干净安装换 uid 后 I 级被吞） | A | 多次实测 |
| **S-h** | 启动链已拆到 **1 633 行**（原 4 589），`lifecycle/startup/` 10 个文件 | A | `VirtualApplicationManager` |
| **S-i** | golden 采集边界：`breadcrumb begin` → 首个 `cb onActivityResumed`；不截到文件尾（周期性打点不属于启动链） | A | 采集脚本 |

---

## 二、已知缺陷（各有结论或待修）

| # | 缺陷 | 状态 |
| --- | --- | --- |
| L3 | `bin.mt.plus` 启动即崩（`NoClassDefFoundError` @ `createApplication` ← legacy Step 2） | **待修**（批 3 先做"可诊断"） |
| L4 | framework 下 via provider 成功率与 legacy 不一致 | 待观测（根因未定） |
| L5 | `mInitialApplication` 身份（`v138-appctx` 显示 `com.nextvm.app -> com.nextvm.app`） | 待修（行为变更） |
| L6 | 运行期加载库不 patch（`libnativeloader` 不 patch + `nativeLoad` hook 未生效） | 已用 +5s/+20s 兜底；触发点另立计划 |
| L11 | `Standard00` 与 guest 内部固定名重叠 | 已登记，未修 |
| — | `slot released stubs slot=1 killed=1/1`（起第二个 Activity 时进程被回收） | 复现 2 次，待观测（见「册-进程与slot」） |

---

## 三、待确认 / 可能错

| # | 事项 |
| --- | --- |
| 1 | `P25/P26`（PopupWindow/BadToken）失败是"容器窗口链不完整"还是"探针取样时机"（已倾向后者：`P27 token=false`） |
| 2 | golden 的"两次逐行相等"只覆盖同一版本；跨容器版本必然不同（`(deleted)` 那次多一帧栈就是例子） |
| 3 | 早期窗口的精确时长（经验 10s，未测边界） |

---

## 四、取数与判据

```powershell
# 采集 golden（1 份 = 1 次启动；脚本会 STOP + LAUNCH 并复位开关）
.\tools\device\capture-breadcrumb-golden.ps1 -Guest mark.via -Tag via-1
# 归一化入库 + 契约守卫
node tools/gen/gen-breadcrumb-golden.mjs --check
```

判据：8 份 golden 两两逐行一致；C1–C12 全过；`bind complete (framework|legacy path)` 各 1 次。

---

## 五、原始文档索引

| 文档 | 用途 |
| --- | --- |
| `架构/架构-启动链契约.md` | **契约 C1–C12 + 归一化规则 + 重采步骤**（首选） |
| `架构/架构-两条bind路径的由来.md` | 为什么有两条路径 |
| `架构/架构-就绪依赖与早期窗口.md` | 10s 窗口的原理与三态判读 |
| `设计/设计-启动编排测法与标准行为来源.md` | 测法来源 |
| `计划/计划-启动链已知缺陷（重构后修复）.md` | L1–L11 缺陷清单与判据 |
| `计划/计划-重构阶段任务总表.md` | R1 各批（B4-4f′/g/g′/h） |
| `架构/架构-进程名与包名身份模型.md` | 身份三层（系统面/管理面/guest 可见面） |
