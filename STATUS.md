# 项目状态（STATUS）

> 最后更新：**2026-11-26（容器 `7d25feb` / versionCode 122：STOP + guest 自检 + S7/S8 已上机）**
> 本文件只回答「当前状态」。文档怎么找见 `docs/README.md`；
> **历史事实/边界**见 `docs/输出/记录/记录-STATUS20261125-归档.md`；
> **被推翻的结论**见 `docs/archive/错误结论-2026-11/`（引用任何结论前先对照一遍）。

---

## 0. 一句话现状

容器可跑 Via 与探针；**WebView 的 `file://` MIME 问题仍在**（Via 主页显示源码）——与"豁免=零欺骗"冲突，出路是 hook `loadUrl` 或内置 WebView（未做）。

**B 阶段：诊断/探针已从启动链清干净，诊断输出与自检体系已成型。**

## 0.1 2026-11-26 本轮成果

| 项 | 结果 | 证据 |
| --- | --- | --- |
| 诊断输出统一 | `DiagChannel` 超限清空 → **环形保留尾部**；新增 `DiagOutput`（唯一落盘出口）；17 处直写收编 | `docs/输出/变更/20261126-l1-诊断输出统一与环形.md` |
| 诊断白名单两端一份配置 | `diag_channels.tsv`（**shared / kotlin / cpp 三段**）→ 生成 `diag_channels.inc`；`check-diag-channels.mjs` 校验 | 同上 + `tools/gen/gen-diag-channels.mjs` |
| 路径匹配统一 | 新增 `PathPattern`（Kotlin）/ `path_pattern.{h,cpp}`（C++），支持 `EXACT` / `PREFIX`（含根形态）/ `GLOB`；`PathRuleStore` 与 `redirect_path` 都走它 | `docs/输出/变更/20261126-诊断白名单与路径匹配统一.md` |
| 容器自检 | `SELF_CHECK` **6 → 8 项**（+S7 真实规则抽样、+S8 诊断写入不计 total）；**主进程 + guest 双进程**（请求文件 + 首次 resume 消费），仅 debug 包 | `docs/输出/变更/20261126-selfcheck-guest.md` |
| 实例停止（缺陷修复） | 原 `killProcess` 只改记录不杀进程 → 新增 `GuestProcessStopper`（SIGKILL）+ `CmdReceiver STOP`；STOP 后二次 LAUNCH 产生**新 bind** | `docs/代码镜像/core/virtualization/lifecycle/GuestProcessStopper.md` |
| 过时发现 | `J19/J20` 在容器刚启动时稳定 `NameNotFound`，**重启+等 10s 自愈** → 归为"就绪依赖"，见 `docs/架构/架构-就绪依赖与早期窗口.md` | 实测三轮 |

| 项 | 状态 |
| --- | --- |
| `VirtualApplicationManager` | 2337 → **2350 行**（增 S7/S8/自检消费；B4-4 未做） |
| 诊断/探针去向 | `core/virtualization/diag/{dump,probe,trace,suppress}` + `ContainerSelfCheck` + `GuestSelfCheck` + `SelfCheckRequest` |
| 探针 | 96 → **98 项**（P24–P27 缺项记账，项数稳定）；`round` 实测 `ok 55 / fail 4 / skipped 39` |
| 构建 | `abiFilters=arm64-v8a`（APK ~21MB）；`module-test` 模块级 matrix（含 `:core:common`） |
| 设备包 | `1.0.0-7d25feb`（versionCode 122）= STOP + guest 自检 + S7/S8 + 环形诊断 |

> 口径：`versionName` / asset 名用**主仓库** `GITHUB_SHA`，**不代表容器代码版本**；
> 容器代码看 CI 日志 `ref=` 与 `git log -1`（本轮 `bc73808`）。

---

## 1. 能力状态

**唯一权威表**：`docs/现状/README.md` 的「能力生效状态」+ `docs/现状/容器能力清单与回归方法.md`（14 项，含验证命令）+ `docs/现状/现状-容器自测覆盖矩阵.md`（A–F 手段 × 21 功能面）。

| 能力 | 状态 |
| --- | --- |
| Binder 代理 / `VirtualContext` 路径改写 / native GOT patch | ✅ |
| guest 路径重定向规则（v142 起） | ✅ |
| Java 层 I/O 重定向（默认 `debug.nextvm.javaIo=0`） | ✅ 开关可切 |
| 路径欺骗豁免（via 零欺骗、仍隔离） | ✅ 已装包验证 |
| 合成路径「族根」翻译（ROOT_FORM，Java+C++） | ✅ |
| 容器自检（8 项 × 主+guest 双进程） / 诊断通道 | ✅ |
| 实例停止（STOP，真杀进程） | ✅ 本轮新增 |
| WebView `file://` MIME | ❌ |
| 跨进程文件选择器 / NIO / 多实例 / 反检测接线 | ❌ / ⏸ |

---

## 2. 未决问题与下一步

| # | 问题 | 现状 / 下一步 |
| --- | --- | --- |
| 1 | **B4-4 启动链拆分** | 前置：**4 份 breadcrumb golden**（2 guest × 2 次）待采；采集脚本 `tools/device/capture-breadcrumb-golden.ps1` 需改成 `STOP + LAUNCH`（STOP 已可用） |
| 2 | **WebView MIME** | 已知（F1–F13 归档）；两条路（hook `loadUrl` / 内置 WebView）均未做 |
| 3 | `J19/J20` 早期窗口 | 容器启动 10s 内 `getApplicationInfo(自己)` 会 `NameNotFound`；根治方案见 `架构-就绪依赖与早期窗口.md` §三（INSTALL 后立即入 PM 表） |
| 4 | 探针 `P25/P26` fail | `BadTokenException`（`decor token=false attached=false`）——待确认是探针取样时机还是窗口包装缺陷 |
| 5 | 已知边界（不再当缺陷） | 字面量宿主路径与 early external 仍写宿主（`javaIo=0`，与 J26/J27 同源）；见 `docs/输出/调研/调研-AOSP-hook契约对照.md` |
| 6 | 探针 `ProbeRunner` 剩余拆分（R1） | 不紧急；前置项集合契约已建立（99 项，`check-probe-ids.mjs`） |

---

## 3. 设备与构建环境

| 项 | 值 |
| --- | --- |
| 测试设备 | `10AFA8169Z0039G`（vivo V2463A / Android 15 / arm64-v8a） |
| 设备时钟 | 比 PC 快约 2 分钟；日志按**设备时间**切片 |
| 容器 | `1.0.0-<7位sha>`（`versionCode = GITHUB_RUN_NUMBER`） |
| 一个容器 uid | 干净安装会变 → guest 的 `Log.i` 可能被吞（诊断写文件）；D/V 级 logcat **被 ROM 过滤**，I/W/E 可见 |
| appops | 干净安装清 `MANAGE_EXTERNAL_STORAGE` → 装后必须重新授予 |
| 签名 | 固定 keystore（`apksigner` SHA-256 校验） |
| 工具链 | Gradle 8.9 / NDK 27.1.12297006 / CMake 3.22.1 |

### CI 纪律

```powershell
node tools/release/gh-api.mjs --dispatch container-build.yml main ref="baseline-v150"  # 容器
node tools/release/gh-api.mjs --dispatch probe-build.yml main                          # 探针
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" modules=":core:common :core:hook"
```

- `push` 不触发任何编译；一次只 dispatch 一个编译；
- 容器 workflow：单测前置、产物先进 Release；`module-test` matrix 含 `:core:common`（**2026-11-26 修**）；
- 假绿守卫：`module-test` 每个 matrix job 现在会校验 `tests>0`。

---

## 4. 文档导航

| 想了解 | 去哪 |
| --- | --- |
| 文档总索引 / 写作约定 | `docs/README.md` |
| 系统怎么运作 | `docs/架构/README.md`；本轮新增 **`架构-就绪依赖与早期窗口.md`** |
| 为什么这么设计 | `docs/设计/README.md` |
| 现在什么生效 / 自测能测什么 | `docs/现状/README.md`、`现状-容器能力清单与回归方法.md`、`现状-容器自测覆盖矩阵.md`、`现状-容器重要部位与依赖.md` |
| 做什么、什么顺序 | `docs/计划/README.md` |
| 证据 / 变更 / 调研 / 记录 | `docs/输出/README.md` |
| 哪些结论是错的 | `docs/archive/错误结论-2026-11/README.md` |

---

## 5. 排查第一站

> guest 行为异常但没崩溃/没栈 → 先看 `GuestBreadcrumb`（logcat tag `BootCrumb`，文件 `<沙箱>/files/breadcrumb-<pid>.log`）；再判是否"早期窗口"（`docs/架构/架构-就绪依赖与早期窗口.md`）；最后才看探针 P/N/W 系列与 native 栈。
