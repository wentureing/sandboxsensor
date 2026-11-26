# path_pattern（core/hook/src/main/cpp/core/path_pattern.h / .cpp）

## 职责

**负责**：C++ 侧的路径模式匹配——`Exact` / `Prefix`（含根形态）/ `Glob`（`*`）。纯逻辑，无 Android/JNI 依赖，可被 host `g++` 直接编译进测试。

**不负责**：规则表遍历与"最长胜"选择（`path_rules.cpp` 的 `redirect_path`）、隐藏集合存储、规则注册（Kotlin 侧）。

## 函数

| 签名 | 作用 | 前提 |
| --- | --- | --- |
| `parse_pattern(raw)` | 含 `*` → `Glob`，否则 `Prefix` | 与 Kotlin `PathPattern.parse` 一致 |
| `pattern_matches(pat, path)` | 匹配 | `Prefix` 额外支持根形态 |
| `pattern_is_root_form(pat, path)` | 根形态判定 | 仅 `Prefix` |
| `glob_match(pat, path)` | `*` 匹配任意字符序列（含 `/`） | 双指针回溯，无 `<regex>` |

## 偏差登记

| 偏差 | 为什么 | 类型 | 观测 |
| --- | --- | --- | --- |
| 不引 `std::regex` | NDK `<regex>` 体积大、回溯不可控；匹配在每次 `open` 热路径 | 必要偏差 | `path_pattern_test.cpp` G1–G8 |
| `Glob` 的 `*` 跨 `/` | 与 Kotlin 侧逐字一致 | 必要偏差 | K3 用例 |
| `Exact` 只由调用方显式构造 | 规则表里没有"精确规则"，隐藏走 `unordered_set` O(1) 查表 | 中性偏差 | `is_path_hidden` 保留查表实现 |

## 约束与坑

1. **红线**：本目录只能 include 标准库；加了 `<jni.h>` / `<android/*>` / `<sys/*>` 会让 host `g++` 编译失败（CI 的 `cpp-core-test` 立即红）。
2. **必须与 Kotlin 同步**：`path_pattern_test.cpp` 与 `PathPatternTest.kt` 用例编号一一对应（G/P/E/K），改语义要两边同时改同一组编号。
3. `glob_match` 不允许递归（热路径栈风险）；当前实现只有两个整数游标 + 回溯点。
4. `parse_pattern` 对 `*` 的判定是**唯一**的形态开关——新增形态要在这里加分支，并同步 Kotlin。
