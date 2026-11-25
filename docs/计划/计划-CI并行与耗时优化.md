# 计划：CI 并行与容器编译耗时优化（调查 + 方案，不含实施）

- 日期：2026-11-25
- 状态：**仅调查与计划**（用户要求，暂不改代码/CI）
- 实测基线：容器编译 230s / 单模块单测串行曾 8 分钟

---

## 一、现状数据（实测，非估算）

### 1.1 容器编译 `build-container`（run 45607135986，总 230s）

| 步骤 | 耗时 | 占比 |
| --- | --- | --- |
| **Build container APK**（`:app:assembleDebug`） | **167s** | **72%** |
| Unit tests（`:core:sandbox:testDebugUnitTest`） | 21s | 9% |
| Cache Android SDK | 13s | 6% |
| Cache Gradle home | 13s | 6% |
| Checkout / keystore / 签名校验 / 发布 | ~16s | 7% |

### 1.2 单测 `module-test`

| 形态 | 耗时 |
| --- | --- |
| 单 job 串行跑 3 模块（改前） | #42 **467s 未完**、#44 **522s 未完**（其中一次是测试死循环，已修） |
| 模块级 matrix（改后） | virtualization 61s / hook 28s / sandbox 41s，**并行** |
| 单个测试类实际耗时 | 全部 0–32ms（`TESTTIME` 实测），56 个测试 1m33s 里主要是编译与 worker 启动 |

## 二、调查结论（外部来源）

| 结论 | 来源 |
| --- | --- |
| 把**单测与架构测试拆到不同 job**、单测再分 2 个 JVM 并行，是成熟做法 | [Hephaestus PR #4174](https://github.com/hephaestus-build/Hephaestus/pull/4174) |
| 测试并行手段：`maxParallelForks`（进程级）与 **test sharding**（按类/包分片） | [ridi-oss/proxy-monster commit](https://github.com/ridi-oss/proxy-monster/commit/9c9354c586250a76c628734413c5b408a6716438)、[Gradle 用户手册](https://docs.gradle.org/8.11.1/userguide/userguide.pdf) |
| **matrix job 共享缓存时，只让一个 job 负责写**（其它用只读/restore），否则每个 job 都尝试保存，浪费 30–60s 且互相覆盖 | [GitHub Actions caching 规则](https://github.com/mthines/agent-skills/blob/main/skills/delivery/github-actions-author/rules/caching.md)、[GitHub 官方 dependency caching](https://docs.github.com/en/enterprise-cloud@latest/actions/reference/workflows-and-actions/dependency-caching) |
| `--configuration-cache` 是 Gradle 官方推荐的大仓库提速手段（省配置阶段） | [Gradle 用户手册](https://docs.gradle.org/8.11.1/userguide/userguide.pdf) |

## 三、逐任务判定：哪些适合拆 job？

判据：**拆 job 只在"被拆的两部分能真正并行、且不重复做昂贵工作"时省时间**；
若两个 job 都要先编译同一份代码，拆开只是把墙钟换成 runner 数。

| 任务 | 适合拆 job？ | 理由 |
| --- | --- | --- |
| **Build APK（167s）** | ❌ **不能拆** | 单个 `assembleDebug` 任务；拆开必须重复编译（30+ 模块） |
| **签名校验 / Stage / Publish** | ❌ 不能拆 | 必须紧跟产物；拆开要传 26MB artifact，反而慢 |
| **Unit tests（21s）** | ⚠️ 可选，收益小 | 与 build 并行可省 21s，但测试 job 也要编译同一模块（约 60–90s）→ **净收益为负或接近 0** |
| **cpp-core-test（11s）** | ✅ 已经是独立 job | 收益已拿到 |
| **三模块单测** | ✅ 已 matrix | 61/28/41s 并行（改前串行 8 分钟） |
| **探针构建** | ✅ 已是独立 workflow | 与容器互不影响 |
| **Roolelectric 缓存 / SDK 缓存** | ❌ 不必拆 | 13s+13s 是 restore 本身，拆 job 只会重复 |

**结论：容器编译内部已经没有"值得拆出去"的任务**。167s 是单任务，只能从"减少编译量"入手，不能靠拆 job。

## 四、真正能省的（按收益/风险排序）

| 优先 | 动作 | 预期收益 | 风险 | 验证方法 |
| --- | --- | --- | --- | --- |
| **P0（已做）** | 只编 `arm64-v8a`（`abiFilters`） | native 编译约 1/4；APK 少 3 份 `.so` | 低 | 对比 Build 步骤耗时与 APK 体积 |
| **P0** | 移除 `core:virtualization` 的 Compose 插件 | 5–15s + 依赖解析 | 低（已 grep：该模块**零** `@Composable`/`androidx.compose`） | 对比 Build 耗时 |
| **P1** | `--configuration-cache` | 10–20s（配置阶段） | 中（AGP 8.x 支持，需首次验证；失败可回退） | 连续两次 run 的 Build 耗时 |
| **P1** | matrix 的缓存角色：只让一个 job 写 Gradle 缓存，其余 `cache-read-only` 风格（或 `restore-keys` + 只读 job） | 省 20–40s（避免 3 个 job 各存 700MB） | 低 | 看 cache 步骤耗时与 "Cache saved" 日志 |
| **P2** | 测试进程级并行 `maxParallelForks=2` | 小（56 个测试共 <2s 执行，瓶颈是 worker 启动） | 低 | `TESTTIME` 总耗时 |
| **P2** | `--offline`（依赖已缓存时） | 几秒 | 低（缓存 miss 会失败，需 fallback） | Build 耗时 |
| **❌ 不做** | 缓存 `container/**/build` | 60–90s | **高**：缓存中毒/假绿，与"产物可信"冲突 | — |
| **❌ 不做** | 把 Unit tests 拆成独立 job 与 build 并行 | ≈0 | 低 | — |

## 五、建议顺序

```
已完成：abiFilters(arm64) + module-test matrix + 400+ 行测试打点
下一步：① 移除 core:virtualization 的 Compose 插件 → 测一次
        ② 加 --configuration-cache → 测一次（失败即回退）
        ③ matrix 缓存只写一份 → 测一次
每次只改一项，用同一条命令对比 Build 步骤耗时（当前 167s 基线）
```

## 六、附：为什么"拆 job"在单测侧有效、在构建侧无效

- **单测侧**：3 个模块的测试任务彼此独立，各自编译自己的模块 → 真并行；
- **构建侧**：`assembleDebug` 是一个 DAG 的终点，任何拆分都要求**同一份编译结果**，
  拆开就只能重复编译或传大 artifact → 不划算。

这也正是 [Hephaestus PR #4174](https://github.com/hephaestus-build/Hephaestus/pull/4174)
把"单测/架构测试"拆 job 而不拆"构建"的原因。
