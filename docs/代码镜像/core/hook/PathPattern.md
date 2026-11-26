# PathPattern（core/hook/PathPattern.kt）

## 职责

**负责**：路径模式的**匹配语义**——`EXACT`（整路径相等）、`PREFIX`（前缀 + 根形态）、`GLOB`（`*` 通配）。

**不负责**：规则表的存储与选择（`PathRuleStore`）、规则生成（`PathKinds`）、隐藏判定（`PathRuleStore.hiddenPaths`）。

与 C++ `core/hook/src/main/cpp/core/path_pattern.h` **同构**：两端用例一一对应（`PathPatternTest` ↔ `path_pattern_test.cpp`）。

## 函数

| 签名 | 作用 | 前提 |
| --- | --- | --- |
| `parse(raw)` | 含 `*` → `GLOB`，否则 `PREFIX` | 生产规则表目前全是 `PREFIX` |
| `exact(raw)` | 构造 `EXACT` | 隐藏/白名单这类精确判定用 |
| `matches(path)` | 三种形态的匹配 | `PREFIX` 额外支持根形态 |
| `isRootForm(path)` | 是否为"根形态"命中 | 仅 `PREFIX` |
| `globMatches(pattern, path)` | `*` 匹配任意字符序列（**含 `/`**） | 双指针回溯，无正则 |

## 偏差登记

| 偏差 | 为什么 | 类型 | 观测 |
| --- | --- | --- | --- |
| `GLOB` 的 `*` **跨 `/`** | 简化实现，避免为正则/路径段语义引入复杂度 | 必要偏差（文档化） | `PathPatternTest` K3 |
| 无尾斜杠规则仍是 `PREFIX`（不是 `EXACT`） | 既有语义被 golden 锁定（`PathKinds` 的无尾斜杠变体靠 startsWith 命中子路径） | 必要偏差（保持等价） | `path_vectors.tsv` + `PathPatternTest` P6/P7 |
| 不提供 `REGEX` | NDK 上 `<regex>` 体积与回溯不可控，且匹配在每次 `open` 的热路径 | 中性偏差 | `Kind` 枚举预留位置 |

## 约束与坑

1. **两端必须同时改**：只改 Kotlin 会让 Java 层与 native 层对同一路径给出不同结论（历史故障：Via 不可交互）。
2. 匹配在**热路径**（每次 open/stat），`globMatches` 必须保持 O(n) 且无分配；不要在这里加日志。
3. `PREFIX` 的 `isRootForm` 依赖 `text` 以 `/` 结尾且长度恰为 `path.length + 1`——去掉任何一条都会让 `getDataDir()` 这类"族根"路径漏翻译。
4. 新增形态（如 `REGEX`）时：先扩 `Kind` → 两端实现 → 加 golden 用例 → 再让 `parse` 识别。
