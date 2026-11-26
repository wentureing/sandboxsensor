# 项目状态（STATUS）

> 最后更新：**2026-11-26（容器 `0798d4b` / 设备包 `1.0.0-db5ba0b` vc125；B4-4 a–e 完成，VAM 4589 → 1795）**
> 本文件只回答「当前状态」。文档怎么找见 `docs/README.md`；
> **历史事实/边界**见 `docs/输出/记录/记录-STATUS20261125-归档.md`；
> **被推翻的结论**见 `docs/archive/错误结论-2026-11/`（引用任何结论前先对照一遍）。

---

## 0. 一句话现状

容器可跑 Via 与探针；启动链已从 4 589 行拆到 **1 795 行**，并有 **8 份 golden × C1–C12** 兜底；
**WebView 的 `file://` MIME 问题仍在**（Via 主页显示源码，两条 bind 路径表现相同）——
与"豁免=零欺骗"冲突，出路仍是 hook `loadUrl` 或内置 WebView（未做）。

## 0.1 2026-11-26 本轮成果

| 项 | 结果 | 证据 |
| --- | --- | --- |
| 启动链 golden | 采集脚本重写为 **STOP + LAUNCH**；**8 份样本**（2 guest × 2 次 × framework/legacy 两条路径），归一化后每组**逐行一致** | `docs/架构/架构-启动链契约.md`、`tools/device/capture-breadcrumb-golden.ps1` |
| 契约测试 | `BreadcrumbGoldenContractTest` **C1–C12**（顺序 / 计数 / provider 失败降级 / 豁免分支 / 两条路径 / 两次一致） | 同上 |
| **B4-4 a–e 拆分** | `VirtualApplicationManager` **4 589 → 1 795 行（−61%）**；`lifecycle/startup/` **8 个新文件**；`diag/dump/GuestStackDumps`、`diag/trace/HijackTrace` | 6 份变更文档 + 13 份镜像文档 |
| 两条路径考古 | legacy（上游 2026-03 初版）→ framework（上游 2026-10 `40d5f88` B2）的由来、并存原因、**三处实质差异** | `docs/架构/架构-两条bind路径的由来.md` |
| 缺陷登记 | **10 条（L1–L10）**：stub 泄漏 / slot 共用 / legacy 崩溃 / provider 不一致 / mInitialApplication / patch 缺失 / 早期窗口 / 探针窗口 / datadir 中性 / WebView | `docs/计划/计划-启动链已知缺陷（重构后修复）.md` |
| 干净安装实测 | `1.0.0-db5ba0b` vc125：**SELF_CHECK ok=8 fail=0**、探针 `entries=98 fail=2(J26,J27)`、via `bind complete (framework path)` + `onActivityResumed` | `tools/device/clean-install-and-test.ps1` |
| CI 改进 | `module-test` 加**编译前置 step** + 失败写 job summary；`check-imports` 能抓**嵌套类 import 写法**（本轮 CI 红的真实原因） | `.github/workflows/module-test.yml`、`tools/lint/check-imports.mjs` |
| 流程约定 | AGENTS.md 新增「**等待约定**」（容器 10s / guest 只看 golden / 判定只读文件）与「**规则 19 脚本途径**」（多步逻辑用 Node） | `AGENTS.md` |

| 项 | 状态 |
| --- | --- |
| `VirtualApplicationManager` | **1 795 行**（原 4 589，−61%） |
| 测试 | `core:virtualization` **119 条**（本轮新增 G1–G4 / H1–H4 / S1–S3 + 契约扩到 8 样本） |
| 探针 | 98 项；`round` 实测 `ok 53 / fail 2(J26,J27) / skipped 43` |
| 构建 | `abiFilters=arm64-v8a`；`module-test` 模块级 matrix + 编译前置 |
| 设备包 | `1.0.0-db5ba0b`（vc125）**干净安装通过** |

> 口径：`versionName` / asset 名用**主仓库** `GITHUB_SHA`，**不代表容器代码版本**；
> 容器代码看 CI 日志 `ref=` 与 `git log -1`（当前 `0798d4b`）。

---

## 1. 能力状态

**唯一权威表**：`docs/现状/README.md`「能力生效状态」+ `docs/现状/容器能力清单与回归方法.md`（14 项）
+ `docs/现状/容器自测覆盖矩阵.md`（A–F 手段 × 21 功能面）+ `docs/现状/现状-启动链测试覆盖与可测试性.md`。

| 能力 | 状态 |
| --- | --- |
| Binder 代理 / `VirtualContext` 路径改写 / native GOT patch | ✅ |
| guest 路径重定向规则（v142 起） | ✅ |
| Java 层 I/O 重定向（默认 `debug.nextvm.javaIo=0`） | ✅ 开关可切 |
| 路径欺骗豁免（via 零欺骗、仍隔离） | ✅ 已装包验证 |
| 合成路径「族根」翻译（ROOT_FORM，Java+C++） | ✅ |
| 容器自检（8 项 × 主+guest 双进程） / 诊断通道 | ✅ |
| 实例停止（STOP，真杀进程） | ✅ |
| **guest 启动链顺序断言（C1–C12 × 8 样本）** | ✅ 本轮新增 |
| **干净安装一键冒烟**（安装→授权→导入→三连判定） | ✅ 本轮新增 |
| WebView `file://` MIME | ❌ |
| 跨进程文件选择器 / NIO / 多实例 / 反检测接线 | ❌ / ⏸ |

---

## 2. 未决问题与下一步

| # | 问题 | 现状 / 下一步 |
| --- | --- | --- |
| **1** | **B4-4 剩余批次 f/g/h** | **a–e 已完成**。下一步 **B4-4f**：legacy attach 链（`attachBaseContext` + `injectGuestPathsIntoLoadedApk` + `disableCheckJniForGuest`）+ 壳/运行时修正（`patchJiaguStubBeforeAttach` + `installRuntimeLoadCallerFix`）→ g（framework bind + 取证）→ h（编排）。边界见 `docs/计划/计划-B4-4剩余批次边界.md` |
| **2** | **L1 stub 泄漏（最影响操作）** | 每 slot 只够 **5 次**启动，超了 `LAUNCH_ACTIVITY` 报 `No available stub`，必须重启容器。登记为 L1，**重构后第一步修** |
| 3 | L2 两实例共用 `processSlot` | `LIST` 可见 via 与 probe 同为 `slot=1`；与 L1 叠加更早失败。重构后修 |
| 4 | L3 legacy 回退路径加固 guest 崩溃 | `bin.mt.plus` `NoClassDefFoundError: l.۠᩹ܽ` @ `createApplication`；framework 侧在 `makeApplication` 里同样炸（壳 so `UnsatisfiedLinkError`）。重构后修 |
| 5 | L4/L5/L6 两条路径不一致 | provider 成功率、`mInitialApplication`、legacy 三段 patch 框架侧缺失。**拆分时不许顺手对齐**（那是行为变更） |
| 6 | WebView MIME | 已知（F1–F13 归档）；两条路（hook `loadUrl` / 内置 WebView）均未做；**两条 bind 路径同样表现**（本轮实测） |
| 7 | `J19/J20` 早期窗口 | 容器启动 10s 内 `getApplicationInfo(自己)` 会 `NameNotFound`；根治见 `架构-就绪依赖与早期窗口.md` §三 |
| 8 | 探针 `P25/P26` | 窗口 token / `UiProbe` 未就绪 → 现表现为 `skipped`（不是 fail）；属探针取样时机 |
| 9 | 探针 `J26/J27` fail = **设计故意** | 探针用字面量宿主路径检测"写穿"；`javaIo` 默认 `0` → Java 层不翻译 → `ENOENT`。判读跳过 |
| 10 | `ProbeRunner` 剩余拆分（R1） | 不紧急；项集合契约已建立（99 项，`check-probe-ids.mjs`） |

---

## 3. 设备与构建环境

| 项 | 值 |
| --- | --- |
| 测试设备 | `10AFA8169Z0039G`（vivo V2463A / Android 15 / arm64-v8a） |
| 设备时钟 | 比 PC 快约 2 分钟；日志按**设备时间**切片 |
| **干净安装后的日志** | **logcat 拿不到该 uid 的 I 级日志**（`logcat --pid=<容器>` 全空）→ 判定只读 `run-as` 下的文件 |
| 容器 | `1.0.0-<7位sha>`（`versionCode = GITHUB_RUN_NUMBER`）；设备当前 `1.0.0-db5ba0b` vc125 |
| appops | 干净安装清 `MANAGE_EXTERNAL_STORAGE` → 装后必须重授（脚本已自动做） |
| 签名 | 固定 keystore（`apksigner` SHA-256 校验） |
| 工具链 | Gradle 8.9 / NDK 27.1.12297006 / CMake 3.22.1 |

### CI 纪律

```powershell
node tools/release/gh-api.mjs --dispatch container-build.yml main ref="baseline-v150"  # 容器
node tools/release/gh-api.mjs --dispatch probe-build.yml main                          # 探针
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:virtualization" test_filter="com.nextvm.core.virtualization.*"
```

- `push` 不触发任何编译；一次只 dispatch 一个编译；
- `module-test`：**编译前置 step**（`compileDebugKotlin`）红了就别指望有测试报告；失败时 job summary 里有根因行；
- 假绿守卫：每个 matrix job 校验 `tests>0`。

---

## 4. 文档导航

| 想了解 | 去哪 |
| --- | --- |
| 文档总索引 / 写作约定 | `docs/README.md` |
| 系统怎么运作 | `docs/架构/README.md`；本轮新增 **`架构-启动链契约.md`**、**`架构-两条bind路径的由来.md`** |
| 为什么这么设计 | `docs/设计/README.md` |
| 现在什么生效 / 自测能测什么 | `docs/现状/README.md`、`现状-容器能力清单与回归方法.md`、`现状-容器自测覆盖矩阵.md`、`现状-启动链测试覆盖与可测试性.md` |
| 做什么、什么顺序 | `docs/计划/README.md`；本轮新增 **`计划-B4-4剩余批次边界.md`**、**`计划-启动链已知缺陷（重构后修复）.md`** |
| 证据 / 变更 / 调研 / 记录 | `docs/输出/README.md`（本轮 6 份变更 + `调研-启动链这类模块的自动化测试方式.md`） |
| 哪些结论是错的 | `docs/archive/错误结论-2026-11/README.md` |

---

## 5. 排查第一站

> guest 行为异常但没崩溃/没栈 → 先看 `GuestBreadcrumb`（tag `BootCrumb`，文件 `<沙箱>/files/breadcrumb-<pid>.log`）；
> 再判是否"早期窗口"（`docs/架构/架构-就绪依赖与早期窗口.md`）；最后才看探针 P/N/W 系列与 native 栈。
>
> **干净安装后不要用 logcat 判定** —— 换 uid 后 vivo 吞 I 级日志，读 `files/checks.log` / `debug-trace.log`。
