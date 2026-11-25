# 变更：C++ 纯逻辑抽取 + 与 Kotlin 共用 golden 向量（S1）

- 日期：2026-11-25
- 状态：**执行中**（等 `module-test` 的 `cpp-core-test` job 验证）
- 性质：**抽取 + 加测试**，不改变行为；C++ 测试作为**并行 job**，不干扰 Java/Kotlin 单测
- 依据：`docs/架构/架构-C++测试夹具设计.md`（S1）、`docs/输出/调研/调研-高频修改热点与测试优先级.md`

---

## 一、目标

`native-hook.cpp` 是 churn 第 1（34 次提交 / 1 993 行改动）但零自动化。
把**可纯逻辑化的部分**抽到独立 TU，让 CI 在 host 上用 g++ 跑向量；
向量与 Kotlin **共用同一份 TSV**，防止 Java/C++ 语义再次分叉。

## 二、影响范围

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `core/hook/src/main/cpp/core/path_rules.h` | **新增** | `RuleTable` / `HiddenSet` / `trim_trailing_slashes` / `is_path_hidden` / `redirect_path`（纯 C++17，禁 Android 头） |
| `core/hook/src/main/cpp/core/path_rules.cpp` | **新增** | 与 Java `translatePath` **逐字一致**的匹配 + 四分支拼接 |
| `core/hook/src/main/cpp/native-hook.cpp` | 修改 | 删掉原 `is_path_hidden`/`trim_trailing_slashes`/`redirect_path` 实现，改为薄包装（绑定 `g_hidden_paths`/`g_path_redirects`）；等价族 `/data/user/0/`→`/data/data/` 兜底保留在薄包装里 |
| `core/hook/src/main/cpp/CMakeLists.txt` | 修改 | `add_library` 增加 `core/path_rules.cpp` |
| `core/hook/src/test/cpp/path_rules_test.cpp` | **新增** | host 向量测试（自写 ASSERT，无 gtest） |
| `core/hook/src/test/resources/golden/path_vectors.tsv` | **新增** | 16 条向量，**Java/C++ 共用** |
| `core/hook/src/test/.../path/PathGoldenTest.kt` | **新增** | Kotlin 侧读同一份 TSV |
| `.github/workflows/module-test.yml` | 修改 | 新增**并行** job `cpp-core-test`（g++ 编译 + 跑向量） |

## 三、关键决策

| # | 决策 | 理由 |
| --- | --- | --- |
| 1 | 只抽 3 个函数（trim / hidden / redirect） | S1 最小集；`hooked_*` 依赖 `real_*`，留给 S2（B2 拆包时） |
| 2 | 薄包装保持原签名 | `hooked_*` 的 24 个调用点一行不改，风险最小 |
| 3 | 拼接实现采用 **Java 的四分支** | C++ 原来是无脑 `to + substr`；两在"from 带斜杠 + to 无斜杠"时不同。当前规则集无该组合（不可观测），但共用向量要求两侧一致 |
| 4 | 保留等价族兜底（仅薄包装） | 不改行为；纯函数与 Java 一致（无兜底）。文档登记为"待统一" |
| 5 | CI 用**独立 job** 并行 | 用户要求：不干扰 Java/Kotlin 测试；也避免串行阻塞 |
| 6 | 向量只描述行为 | 新增族时 `PathKinds` 与 TSV 同提交，避免"第二份规格"漂移 |

## 四、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 抽取改了语义 | 用**同一份 16 条向量**约束两侧；本地已用独立脚本预验"向量 ↔ 算法"一致（16/16） |
| 2 | 本地无 C++ 编译器 | 已做结构自检（括号/字符串扫描）；最终以 CI `cpp-core-test` 为准 |
| 3 | Android 构建缺 `path_rules.cpp` | `CMakeLists.txt` 已加入；Android 侧编译错误会在 `container-build` 暴露 |
| 4 | 等价族兜底造成 Java/C++ 差异 | 向量**不含**"只注册 `/data/data/`、查 `/data/user/0/`"的组合；文档登记待统一 |

## 五、回退

```powershell
git -C _ref/ShitJumpingVM revert <本次 commit>
```

## 六、验证

| 项 | 判据 |
| --- | --- |
| C++ 向量 | `cpp-core-test` job：`path_rules_test: 16/16 passed` |
| Kotlin 向量 | `PathGoldenTest`（与 C++ 同一份 TSV） |
| 不干扰 | `module-test` job 与 `cpp-core-test` job **并行**，互不阻塞 |
| 语义未变 | 既有 T1–T43 / K1–K7 / H1–H13 / R1–R12 全绿 |
| Android 构建 | `container-build` 能编过（含 `core/path_rules.cpp`） |

## 七、实际变更

（CI 通过后补 `git diff --stat`）
