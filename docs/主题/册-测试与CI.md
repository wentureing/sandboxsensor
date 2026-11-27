# 册：测试与 CI（2026-11-27）

- **时效**：2026-11-27 复核。触发复核：① CI job/门禁调整 ② 新增测试层（host/instrumentation）③ 签名或缓存策略变化
- **可信度**：A=实测（CI run）/ B=代码级
- **替代**：集中 `计划-CI并行与耗时优化.md`、`计划-黄金测试与覆盖率提升.md`、`设计/设计-拆分标准流程.md`、
  `设计/设计-重构行为等价性验证.md`、`设计/设计-调试插桩开销评估.md`、`设计/设计-技术路线对比.md`（测试部分）的结论；原文保留
- 一句话：**门禁 = 毫秒级静态 → module-test（含 host C++ 与 compile-app）→ container-build → 真机 5 项**；CI **只能手动 dispatch，一次一个**

---

## 一、测试分层（能证什么 / 不能证什么）

| 层 | 跑什么 | 能证明 | 不能证明 |
| --- | --- | --- | --- |
| **纯 JVM**（JUnit + golden TSV） | `BreadcrumbGoldenContractTest`（8×C1–C12）、`PathGoldenTest`、`PathWhitelistEffectTest`、lib/path 向量测试 | 纯函数逻辑、顺序/计数/分支存在性 | "是否真的挂上了" |
| **host C++**（`cpp-core-test`） | `path_rules` / `path_pattern` / `path_spoof` / `lib_filter` + golden TSV | 纯逻辑（可 host 编译部分） | Android 运行时行为 |
| **Robolectric** | 部分 Kotlin 测试 | Android API 交互分支 | native / IPC / 真实 WebView / 进程顺序 |
| **真机探针**（`probe/`） | 102 项（J/N/P/F/D/H/X 系列） | 端到端真实生效 | 定位到具体环节（除非有分步日志） |
| **真机自检**（SELF_CHECK S1–S8） | 容器动作可行性 | 容器侧链路 | guest 视角一致性 |

> **一句话**：探针通过 ≠ 应用可用（v141 案例）。三层都要有。

---

## 二、CI 门禁（顺序与判据）

| 层 | 命令 | 判据 |
| --- | --- | --- |
| **T0 静态**（毫秒） | `kotlin-lint` / `check-imports`（跨模块）/ `check-duplicate-imports` / `check-test-fn-names` / `check-diag-channels` / `check-probe-ids` / `check-jni-contract`（改 native 时）/ `verify-deleted-suffix --strict` | 全过 |
| **T1 等价** | `check-refactor-equivalence --old-file --old-fn --new-file --new-fn` | 等价 ✓ 或偏差已登记 |
| **T2/T3** | `module-test.yml`（matrix：common/hook/sandbox/virtualization + `compile-app` + `cpp-core-test`） | 绿 + `tests>0`（假绿守卫） |
| **T4 真机** | 能力清单至少 5 项（探针 diag / stackdump / 宿主写入 / 豁免名单 / breadcrumb 尾部） | 全过 |
| 容器编译 | `container-build.yml` | 绿（native/CMake 门禁） |

---

## 三、CI 纪律（硬约束）

| 规则 | 内容 |
| --- | --- |
| **手动 dispatch** | 两个编译 workflow **只有** `workflow_dispatch`；`push` 不触发任何编译 |
| **一次一个** | 同一时刻只跑一个编译 CI；同 workflow 不许两个 run |
| **显式指定** | 触发时必须说清是 `probe-build.yml` 还是 `container-build.yml` |
| **不等结果** | 触发后继续下一步，稍后单独查（容器 3–5 分钟、探针 2–3 分钟） |
| **假绿守卫** | 每个 matrix job 校验 `tests > 0`；`compile-app` job 只编 `:app`（带全部 `:feature:*`）防类型错误漏到 container-build |
| **产物格式** | 必须 `.apk`，走 Release asset（`apk-latest`），不用 artifact（会被 zip） |
| **签名固定** | `Cache debug keystore`（key `android-debug-keystore-v1`）；漂移会导致 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`（会连带清 appops 与沙箱数据） |

---

## 四、现行结论 / 已知问题

| # | 结论 | 等级 |
| --- | --- | --- |
| **C-a** | 容器代码在独立仓库（`wentureing/ShitJumpingVM`），主仓库 CI 通过 `actions/checkout` 拉它 ⇒ **主仓库 push 与容器代码无关**，容器永远手动 | A |
| **C-b** | `module-test` 的 matrix 只编 `:core:*`；`:app`/`:feature` 的类型错误曾两次漏到 `container-build`（5 分钟）→ 新增 `compile-app` job | A |
| **C-c** | `cpp-core-test` 只编 2–4 个 TU，秒级；不新增 workflow（挂在 module-test 里） | A |
| **C-d** | 下载 Release asset **必须用浏览器式 URL**（`github.com/.../releases/download/...`）；API asset 路径实测 90s 内只下 4MiB（风控/限速） | A |
| **C-e** | 代理 `127.0.0.1:12450` 与直连**都可用**（实测各 4 次 API 调用均 HTTP 200，直连更快 ~1.0s vs 1.4–2.6s） | A |
| **C-f** | `push-https.ps1` 在 token.txt 改多 PAT 后**必 401**（旧版 `Get-Content -Raw` 把两行拼成一个 token + RepoRoot 少一层）→ 已修 | A |

---

## 五、待确认 / 可能错

| # | 事项 |
| --- | --- |
| 1 | `module-test` 单跑一个模块时，其余三个 job 会跳过 `Compile/Run/Assert`（条件步骤）——这会让"全绿"看起来像"全测过"（守卫已修 `Show diagnostics sequence` 的误报） |
| 2 | 缓存键固定（`CACHE_EPOCH`）——升 epoch 会让首轮变慢，但旧 epoch 缓存无人回收（`--delete-cache` 手动） |
| 3 | 两个 workflow 并行时共用 Gradle/Android SDK 缓存键，可能"缓存保存失败"（不影响结果） |
| 4 | `check-hook-coverage.mjs` 依赖本地 AOSP 源码副本（`tools/tmp/libcore-src`），干净环境会跳过 |

---

## 六、原始文档索引

| 文档 | 用途 |
| --- | --- |
| `计划/计划-CI并行与耗时优化.md` | 耗时分析与方案（未实施） |
| `计划/计划-黄金测试与覆盖率提升.md` | golden 与覆盖率计划 |
| `设计/设计-拆分标准流程.md` | 拆分四件套（代码/测试/镜像文档/真机判据） |
| `设计/设计-重构行为等价性验证.md` | 等价性检查方法与已知的结构性允许偏差 |
| `设计/设计-调试插桩开销评估.md` | 插桩成本门槛 |
| `架构/架构-C++测试夹具设计.md` | host 测试设计（S1 落地 / S4 暂停） |
| `主题/册-探针与测试体系.md` | 探针 102 项与三套体系（互补） |
