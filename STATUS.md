# 项目状态（STATUS）

> 最后更新：**2026-11-25 21:30（容器 47c4763 已装包验证）**
> 本文件只回答「当前状态」。文档怎么找见 `docs/README.md`；
> **历史事实/边界**见 `docs/输出/记录/记录-STATUS20261125-归档.md`；
> **被推翻的结论**见 `docs/archive/错误结论-2026-11/`（引用任何结论前先对照一遍）。

---

## 0. 一句话现状

容器可跑 Via 与探针；**WebView 的 `file://` MIME 问题仍在**（Via 主页显示源码）——它与"豁免=零欺骗"冲突，出路是 hook `loadUrl` 或内置 WebView（未做）。

**B 阶段进展：容器自身的诊断/探针已从启动链清干净。**

| 项 | 状态 |
| --- | --- |
| `VirtualApplicationManager` | **4589 → 2337 行（−49%）**，只剩启动链 + 装配 |
| 诊断/探针去向 | `core/virtualization/diag/{dump,probe,trace,suppress}` + `ContainerSelfCheck` |
| 容器自检 `SELF_CHECK` | ✅ 真机 `hookcheck ok=6 fail=0` |
| 诊断通道（D 系列） | ✅ `D01–D05` 全 ok |
| 构建 | `abiFilters=arm64-v8a`：APK **25.84MB → 21.05MB**；module-test 改模块级 matrix |
| 设备包 | `1.0.0-47c4763`（versionCode 113），SELF_CHECK + 探针 round 已验证 |

---

## 1. 能力状态

**唯一权威表**：`docs/现状/README.md` 的「能力生效状态」+ `docs/现状/容器能力清单与回归方法.md`（14 项，含验证命令）。

要点速览：

| 能力 | 状态 |
| --- | --- |
| Binder 代理 / `VirtualContext` 路径改写 / native GOT patch | ✅ |
| guest 路径重定向规则（v142 起） | ✅ |
| Java 层 I/O 重定向（默认 `debug.nextvm.javaIo=0`，可开关） | ✅ |
| **路径欺骗豁免**（via 零欺骗、仍隔离） | ✅ 已装包验证 |
| 合成路径「族根」翻译（ROOT_FORM，Java+C++） | ✅ |
| 容器自检 / 诊断通道 | ✅ |
| WebView `file://` MIME | ❌ |
| 跨进程文件选择器（会拉起系统 DocumentsUI） | ❌ 未接管（仅计划） |
| NIO（`Files.*`）路径翻译 | ⏸ 暂不实现 |
| 多实例 | ⏸ 暂停 |
| `AntiDetectionEngine` / `IdentitySpoofingEngine` | ❌ 零调用 |

---

## 2. 未决问题与下一步

| # | 问题 | 现状 / 下一步 |
| --- | --- | --- |
| 1 | **WebView MIME** | 已知（F1–F13 归档）；两条路（hook `loadUrl` / 内置 WebView）均未做 |
| 2 | **B4-4 启动链拆分** | 前置：先写 8–10 条 breadcrumb 契约测试（bind complete / Provider 顺序 / onCreate / first activity / filesDir 注入 / 生命周期顺序） |
| 3 | 已知边界（不再当缺陷） | 字面量宿主路径与 early external 仍写宿主（`javaIo=0`，与 J26/J27 同源）；`readlink` 泄露 / `setPermission0` 待评估 → 三分类见 `docs/输出/调研/调研-AOSP-hook契约对照.md` |
| 4 | C++ 纯逻辑抽取 | maps 行过滤 / synthetic 路径 / proc_spoof 匹配 → `core/*.cpp` + host g++ 向量 |
| 5 | 反检测接线 | 需先修 `AntiDetectionEngine` 两缺陷（`is_path_hidden` 精确匹配、无 `readdir` 过滤） |

---

## 3. 设备与构建环境

| 项 | 值 |
| --- | --- |
| 测试设备 | `10AFA8169Z0039G`（vivo V2463A / Android 15 / arm64-v8a） |
| 设备时钟 | 比 PC 快约 2 分钟；日志按**设备时间**切片 |
| 容器 | `1.0.0-<7位sha>`（`versionCode = GITHUB_RUN_NUMBER`） |
| 容器 uid | 每次干净安装会变 → **guest 的 `Log.i` 可能被吞**（诊断写文件） |
| appops | 干净安装会清 `MANAGE_EXTERNAL_STORAGE` → 装后必须重新授予 |
| 签名 | 固定 keystore（SHA-256 `60:AD:A6:9E:…:40:B3:2C`） |
| 工具链 | Gradle 8.9 / NDK 27.1.12297006 / CMake 3.22.1 |

### CI 纪律（2026-11-25 更新）

```powershell
node tools/release/gh-api.mjs --dispatch container-build.yml main ref="baseline-v150"  # 容器
node tools/release/gh-api.mjs --dispatch probe-build.yml main                          # 探针
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="<分支>" modules=":core:virtualization"
node tools/release/gh-api.mjs --cancel <run-id>                                        # 旧 run 落后时
```

- `push` 不触发任何编译；一次只触发一个编译
- 容器 workflow：**Unit tests 已恢复且发包前置**（单测失败仍红，但产物已发布）
- `module-test`：**模块级 matrix**（virtualization / hook / sandbox 并行）
- 测试卡死：看 `TESTSTART` 无对应 `TESTTIME`

---

## 4. 文档导航

| 想了解 | 去哪 |
| --- | --- |
| 文档总索引 / 写作约定 | `docs/README.md` |
| 系统怎么运作 | `docs/架构/README.md` |
| 为什么这么设计 | `docs/设计/README.md` |
| 现在什么生效 | `docs/现状/README.md` + `docs/现状/容器能力清单与回归方法.md` |
| 做什么、什么顺序 | `docs/计划/README.md` |
| 证据 / 变更 / 调研 / 记录 | `docs/输出/README.md` |
| 历史事实与边界（F1–F13 等） | `docs/输出/记录/记录-STATUS20261125-归档.md` |
| 哪些结论是错的 | `docs/archive/错误结论-2026-11/README.md` |

---

## 5. 排查第一站

> guest 行为异常但没崩溃/没栈 → 先看 `GuestBreadcrumb`（logcat tag `BootCrumb`，
> 文件 `<沙箱>/files/breadcrumb-<pid>.log`），再看探针 P/N/W 系列，最后才看 native 栈。
> 详细顺序与历史弯路见 AGENTS.md「排查问题的第一站」。
