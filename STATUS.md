# 项目状态（STATUS）

> 最后更新：**2026-11-27 晚**（容器 `6324660` / 设备包 `1.0.0-4f0e9d7` 未动；探针换 `2e7e675`；8 份 golden 重采）
> 本文件只回答「当前状态」。文档怎么找见 `docs/README.md`；
> **历史事实/边界**见 `docs/输出/记录/记录-STATUS20261125-归档.md`；
> **被推翻的结论**见 `docs/archive/错误结论-2026-11/`（引用任何结论前先对照一遍）。

---

## 0. 一句话现状

容器可跑 via 与探针；**process slot 的观测 / 会话复用 / 按 task 清理已落地并真机验证**；
启动链拆到 **1 633 行**（原 4 589），有 **8 份 golden × C1–C12** 兜底；
**native 已建立"关键过程插桩 + 基线"**（干净安装实测：`libs=415 patched=411 skipped_self=1 skipped_system=3`）；
**WebView `file://` MIME 仍未解决**（独立立项）。

## 0.1 2026-11-27 本轮成果

| 项 | 结果 | 证据 |
| --- | --- | --- |
| **process slot 会话复用 + 清理**（Step 0a–3） | 点 guest 复用原进程（pid 不变）；STOP 归还 stub（池可循环）；STALE 自动回收；按 **task** 逐个关闭（不杀进程）；`launchApp(forceNewSession)` 开新窗口 | `docs/输出/变更/20261127-process-slots-会话复用与UI解绑.md` |
| 设置页 / 列表 | 计数 = 真实占用 slot 数（含 ORPHAN）；列表按 slot 一行、可展开 task；`Bring to front` 成败落 `checks.log` | 同上 §3.3 |
| 两个真 bug 修复 | ① `candidatesFor` 未过滤已占用 stub → "New instance" 永远拿 `Standard00`、折叠回同一 task；② 进程名改写误伤主进程 | `c0394e8` / `4b01d3d` |
| **B4-4f 拆分** | `GuestLoadedApkPathInjection` + `PackerAttachFixes` 搬出；VAM 1 795 → **1 633 行**；2/3 函数等价 ✓、1 个判据抽取已登记 | `c91155e`、两份镜像文档 |
| **native 插桩 + 基线** | GOT patch / 延迟 patch / `/proc` spoof 计数 + 第 24 个 JNI 导出 `nativeGetHookStats`；三阶段采样（bind/+5s/+20s） | `docs/现状/现状-native-关键过程基线.md` |
| 探针 N16 | `libc open/read /proc/self/maps`（与 N10 的 `fopen` 对照，覆盖 native spoof 计数）；项集合 golden → **100 项** | `2e7e675`、`docs/输出/探针-检测项清单.md` |
| CI 工具 | `compile-app` job（只编 `:app`+`:feature:*`）；`check-imports` 第 3 类（import 路径错）+ 跨模块；`check-jni-contract`（24 external ↔ C++ 导出） | `.github/workflows/module-test.yml`、`tools/lint/` |
| 文档 | 进程名/包名身份模型、slot/租约术语、native 技术路线、重构阶段总表、多实例缺口、native N1a 计划、诊断通道记录 | 见 §4 |
| **接手后现场更新**（2026-11-27 晚） | 只换探针 guest（`2e7e675`，不动容器 `4f0e9d7`）→ `N16` 验证通过；8 份 breadcrumb golden 按契约文档重采入库 | `docs/输出/变更/20261127-golden重采与探针2e7e675.md` |

| 项 | 状态 |
| --- | --- |
| `VirtualApplicationManager` | **1 633 行**（原 4 589，−64%） |
| `native-hook.cpp` | **7886 行**（N1a 搬出 117 行；29 个 `JNIEXPORT` = 24 契约 + 5 旧符号） |
| 测试 | `core:virtualization` 原 119 条 + 本轮 **S14–S19 / L8–L9 / R1–R8 / U1–U5 / P1–P9 / I1 / R4**（以 CI 报告为准） |
| 探针 | 静态 **100 项**（`check-probe-ids`）；运行时报告 **106 条**（含 7 个重复 id，见 §2 #11）；`suite=round` fail=`P25,P26`（取样时机，见 §2 #12） |
| 设备包 | `1.0.0-4f0e9d7`（`clean-install-and-test.ps1` 通过） |

> 口径：`versionName` / asset 名用**主仓库** `GITHUB_SHA`，**不代表容器代码版本**；
> 容器代码看 `git -C _ref/ShitJumpingVM log -1`（当前 `6324660`）。

---

## 1. 能力状态

**唯一权威表**：`docs/现状/README.md`「能力生效状态」+ `docs/现状/容器能力清单与回归方法.md`（14 项）
+ `docs/现状/容器自测覆盖矩阵.md` + `docs/现状/现状-启动链测试覆盖与可测试性.md` + `docs/现状/现状-native-关键过程基线.md`。

| 能力 | 状态 |
| --- | --- |
| Binder 代理 / `VirtualContext` 路径改写 / native GOT patch | ✅ |
| guest 路径重定向规则（v142 起） | ✅ |
| Java 层 I/O 重定向（默认 `debug.nextvm.javaIo=0`） | ✅ 开关可切 |
| 路径欺骗豁免（via 零欺骗、仍隔离） | ✅ |
| 合成路径「族根」翻译（ROOT_FORM，Java+C++） | ✅ |
| **process slot 观测 / 会话复用 / 按 task 清理** | ✅ 真机验证（pid 不变、`released stubs slot=0 killed=1/1`） |
| 容器自检（8 项 × 双进程）/ 诊断通道 | ✅ |
| 实例停止（STOP，真杀进程） | ✅ |
| 启动链顺序断言（C1–C12 × 8 样本） | ✅ |
| 干净安装一键冒烟 | ✅ |
| **native 关键过程插桩 + 基线** | ✅（`[Dbg/native]` 三阶段；`spoof=0` 有解释） |
| WebView `file://` MIME | ❌ |
| 跨进程文件选择器 / NIO / 多实例 / 反检测接线 | ❌ / ⏸ |

---

## 2. 未决问题与下一步

| # | 问题 | 现状 / 下一步 |
| --- | --- | --- |
| **1** | **B4-4 剩余 f′/g/g′/h** | a–f 已完成（VAM 1 633 行）。下一步 **B4-4f′**：`attachBaseContext` 编排 + `disableCheckJniForGuest`（叶子已搬成 object → 零注入）。见 `docs/计划/计划-重构阶段任务总表.md` R1 |
| **2** | **native 重构第一轮（N1a）** | ✅ **已落地**：`core/path_spoof.{h,cpp}`（6 个纯函数、`guest_pkg` 参数化）+ 33 条 golden 向量 + host 测试；`native-hook.cpp` 8003 → **7886 行**。CI 待跑，见 `docs/输出/变更/20261127-N1a-path-spoof.md`。下一步 **N1b**（`proc_spoof` 拆"读 + 转"） |
| 3 | L2 两实例共用 slot | 属多实例缺口（M4）；用户已同意顺序但**暂缓**，见 `计划-多实例支持缺口清单.md` |
| 4 | L3 legacy 回退崩溃可诊断 | `bin.mt.plus` 崩溃；先补"framework bind FAILED 完整栈 + 明确原因" |
| 5 | L4/L5/L6 两条路径不一致 | provider 成功率 / `mInitialApplication` / legacy 三段 patch；**行为变更，逐条走变更流程** |
| 6 | WebView MIME | 未解决（两种出路均未做） |
| 7 | L11 `Standard00` 与 guest 内部固定名重叠 | 已登记；修法候选：`getStubForApp` 改 `Standard04` 或按活跃 task 避让 |
| 8 | `J19/J20` 早期窗口 | 容器启动 10s 内 `getApplicationInfo(自己)` `NameNotFound`；见 `架构-就绪依赖与早期窗口.md` |
| 9 | 探针 `J26/J27` fail = 设计故意 | `javaIo=0` → Java 层不翻译 → `ENOENT`；判读跳过 |
| 10 | 探针 `N16` **已结案（2026-11-27）** | 换探针 `2e7e675` 后实测 `N16.libc_maps lines=3252 pkg_hit=18`，与 `N10`（`fopen` 路径）一致；`spoof/maps_lines` 仍 0 且解释成立（无 native 读者）。见 `docs/输出/变更/20261127-golden重采与探针2e7e675.md` |
| 11 | 探针报告 `106` 条 / `99` 唯一 id | `ContentProvider.onCreate` 与 `Activity.onCreate` 各跑一轮 → `N11..N16`、`P28` 重复；首轮处于早期窗口故 N 系列首轮 fail。合并时按 id 去重或 provider 阶段不跑 native 项 |
| 12 | `P25/P26` 取样时机 | `UiProbe` 在 `ProbeActivity.onCreate` 跑，`P27` 同时显示 `token=false attached=false` → `BadTokenException`。改到 `onWindowFocusChanged`/`onResume` 之后再跑（探针侧改动） |
| 13 | `(deleted)` 后缀剥离 **已修（2026-11-27）** | 上游 fork 前遗留（`a1871a6`/`e590c32`，woand）：长度手写 11（实际 10）⇒ 剥离永不生效 ⇒ maps 里带该后缀的 guest apk 行被整行丢弃（实测零触发）。已改为 `kDeletedSuffixLen`（`sizeof - 1`，两处共用）+ 3 条向量 + 3 条不变量 + CI 静态守卫。见 `docs/输出/变更/20261127-deleted-suffix-11to10.md` |

---

## 3. 设备与构建环境

| 项 | 值 |
| --- | --- |
| 测试设备 | `10AFA8169Z0039G`（vivo V2463A / Android 15 / arm64-v8a） |
| 设备时钟 | 比 PC 快约 2 分钟；日志按**设备时间**切片 |
| **干净安装后的日志** | logcat 拿不到该 uid 的 I 级日志 → 判定只读 `run-as` 下的文件 |
| 容器 | `1.0.0-<7位sha>`；设备当前 `1.0.0-4f0e9d7` |
| 探针 | 静态 100 项；运行时 106 条（7 个重复 id）；探针包 `2e7e675` |
| appops | 干净安装清 `MANAGE_EXTERNAL_STORAGE` → 装后必须重授（脚本已做） |
| 签名 | 固定 keystore（`apksigner` SHA-256 校验） |
| 工具链 | Gradle 8.9 / NDK 27.1.12297006 / CMake 3.22.1 |

### CI 纪律

```powershell
node tools/release/gh-api.mjs --dispatch container-build.yml main ref="baseline-v150"  # 容器
node tools/release/gh-api.mjs --dispatch probe-build.yml main                          # 探针
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:virtualization" test_filter="com.nextvm.core.virtualization.*"
```

- `push` 不触发编译；**一次只 dispatch 一个**（同 workflow 不许两个 run）；
- `module-test` 的 `compile-app` job 只编 `:app`（带上全部 `:feature:*`），防 app/feature 层编译错误漏到 container-build；
- 假绿守卫：每个 matrix job 校验 `tests>0`。

---

## 4. 文档导航（本轮新增已标注）

| 想了解 | 去哪 |
| --- | --- |
| 文档总索引 / 写作约定 | `docs/README.md` |
| **系统怎么运作** | `docs/架构/README.md`；**新增** `架构-native模块关系与技术路线.md`、`架构-进程名与包名身份模型.md`、`架构-slot租约与进程术语.md`；旧有 `架构-启动链契约.md`、`架构-两条bind路径的由来.md` |
| **为什么这么设计** | `docs/设计/README.md`；**新增** `设计-native重构与临时适配器.md`（含性能评估/选型）、`设计-process-slot测试分层与matrix.md` |
| **现在什么生效** | `docs/现状/README.md`、`现状-容器能力清单与回归方法.md`、**新增 `现状-native-关键过程基线.md`**、`现状-容器自测覆盖矩阵.md` |
| **native 全景（描述+计划一张表）** | **`docs/现状/现状-native全景与计划台账.md`**（2026-11-27 整理：代码事实、生效面、缺口、R2/N1a 状态、旧文档过时清单） |
| **接下来做什么** | `docs/计划/README.md`；**新增** `计划-重构阶段任务总表.md`（R1–R4 + DoD）、`计划-native-N1a-path-spoof.md`、`计划-多实例支持缺口清单.md`；旧有 `计划-B4-4剩余批次边界.md`、`计划-启动链已知缺陷（重构后修复）.md` |
| **做过什么/证据** | `docs/输出/变更/20261127-process-slots-会话复用与UI解绑.md`、`docs/输出/记录/记录-诊断通道与零代码调试手段.md`、`docs/输出/探针-检测项清单.md` |
| 哪些结论是错的 | `docs/archive/错误结论-2026-11/README.md` |

---

## 5. 排查第一站

1. guest 行为异常但没崩溃/没栈 → `GuestBreadcrumb`（`<沙箱>/files/breadcrumb-<pid>.log`，tag `BootCrumb`）；
2. 判是否"早期窗口"（`docs/架构/架构-就绪依赖与早期窗口.md`）；
3. **native 相关** → `docs/现状/现状-native-关键过程基线.md` + `files/debug-trace.log` 的 `[Dbg/native]`；
4. 最后才看探针 P/N/W 系列与 native 栈。
5. **干净安装后不要用 logcat 判定** —— 读 `files/checks.log` / `files/debug-trace.log`。
