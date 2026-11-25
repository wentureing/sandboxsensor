# 架构：C++ 测试夹具设计（专门构造测试代码）

- 日期：2026-11-25
- **核实基准**：容器 `baseline-v150 @ 15147f0`；`native-hook.cpp` 7 468 行 / 326 KB
- 状态：**S1 已落地**（`core/path_rules.{h,cpp}` + 共用 `path_vectors.tsv` + 并行 CI job）；**S4 暂停**（用户 2026-11-25 决定）；S2 待 B2
- 背景：churn 第 1（接手后 **34 次提交、1 993 行改动**）、**零自动化**（`docs/输出/调研/调研-高频修改热点与测试优先级.md`）
- 结论先给：**可以构造测试代码，但只抽「纯逻辑」到独立 TU，不造"模拟 Android"的宿主桩**；
  依赖注入留给 B2 拆包一起做；环境差异靠设备内自检补。

---

## 一、为什么不能"直接给 native-hook.cpp 编个宿主测试"

文件头依赖（实测 `#include` 统计）：

```
jni.h                                  ← JNI 类型与入口
android/log.h, android/dlext.h         ← Android 专有
link.h / dlfcn.h / elf.h               ← GOT/PLT patch（dl_iterate_phdr、elf 结构）
linux/seccomp.h, linux/filter.h, linux/audit.h  ← 诊断
sys/* 一堆 + memfd/prctl/setjmp/ucontext ← 运行时行为
```

要整文件在 host 编译，必须提供 `jni.h`/`android/log.h` 等桩。**这种测试验证的是桩，不是 Android**，
而且桩会随 Android 版本漂移 → 假绿。所以不做。

## 二、三条路线

| 路线 | 做法 | 成本 | 覆盖 | 评价 |
| --- | --- | --- | --- | --- |
| **S1 抽纯逻辑 + host 单测** | 把无 Android 依赖的字符串/查表函数抽到 `cpp/core/`，CI 用 g++ 跑向量 | 中（动调用点，不改语义） | `redirect_path` / `is_path_hidden` / maps 过滤 / 合成路径模板 | ✅ **推荐** |
| S2 依赖注入（hook context） | `real_*` 与规则表收进结构体，`hooked_*` 可注入 stub | 高 | `hooked_open` 等分支 | ⏸ **并到 B2 拆包**，避免两次动刀 |
| S3 宿主桩整文件编译 | 自制 `jni.h`/`android/log.h` | 很高 | 名义上全覆盖，实际测桩 | ❌ 不做 |
| **S4 设备内自检（补充）** | debug 开关在 guest 启动跑同一组向量，写 `debug-trace` | 低 | **真实** linker/JNI/libc 环境 | ⏸ **暂停**（用户 2026-11-25；需要装包验证，成本高于当期收益） |

## 三、S1 的具体设计

### 3.1 抽哪些函数（按依赖分类）

| 函数（当前 `static`） | 可抽 | 需要参数化 |
| --- | --- | --- |
| `trim_trailing_slashes` | ✅ 纯 | — |
| `redirect_path` | ✅ 纯 | `g_path_redirects` → 参数 `const RuleTable&` |
| `is_path_hidden` | ✅ 纯 | `g_hidden_paths` → 参数 |
| `is_proc_self_path` | ✅ 纯 | — |
| `maps_line_visible` | ✅ 纯 | 隐藏关键字表可保留为常量 |
| `synthetic_apk_path` / `synthetic_lib_path` | ✅ 纯 | `g_spoofed_package_name` → 参数 |
| `rewrite_virtual_path` / `transform_maps_line` | ⚠️ 半纯 | 需要 spoof 包名/pid/host 前缀 → 参数化后可测 |
| `build_spoofed_status/stat` | ⚠️ 半纯 | 依赖读文件 → 用 host 文件代替 |
| `hooked_*` 系列 | ❌ | 依赖 `real_*` 与 syscall → S2 |

### 3.2 目录与构建

```
core/hook/src/main/cpp/core/            ← 新增：纯 C++17，**禁止 include Android/JNI 头**
    path_rules.h
    path_rules.cpp
core/hook/src/main/cpp/native-hook.cpp   ← 只保留：JNI 入口、GOT patch、hooked_*、诊断
core/hook/src/test/cpp/                  ← 新增：**不进 Android 构建**
    path_rules_test.cpp                  ← 自写 ASSERT（不依赖 gtest，离线可跑）
    CMakeLists.txt                        ← host 构建（g++/clang++ 均可）
core/hook/src/test/resources/golden/
    path_vectors.tsv                      ← **单一来源**，Java 与 C++ 都读它
```

**为什么向量表要单一来源**：本仓库已经实证过"两份实现必然脱节"（Kotlin 曾用 `String.replace` 全局替换
而 C++ 用 `substr` 是对的）。共用一份 TSV 是唯一能防止 Java/C++ 语义再次分叉的机制。

### 3.3 向量表格式

```tsv
# id	rule_set	path	expected
R_ROOT	app_i1	/data/user/0/mark.via	<sandbox:i1>
R_ROOT_SLASH	app_i1	/data/user/0/mark.via/	<sandbox:i1>/
R_SIBLING	app_i1	/data/user/0/mark.via.pro	/data/user/0/mark.via.pro
R_EXTERNAL_BARE	app_i1	/storage/emulated/0/Android/data/mark.via	<sandbox:i1>/sdcard/Android/data/mark.via
R_SDCARD_ROOT	external	/sdcard	<sandbox:i1>/sdcard
R_HIDDEN	hidden_su	/system/bin/su	/dev/null
R_HIDDEN_SIBLING	hidden_su	/system/bin/sudo	/system/bin/sudo
```

- `rule_set` 是命名规则集：`app_i1` / `external` / `hidden_su`，两边用同一份定义构造；
  `<sandbox:i1>` 由测试代码替换为固定值（Java `"/sandbox/i1"`，C++ 同）。
- **Kotlin 侧**：`PathGoldenTest` 读 `src/test/resources/golden/path_vectors.tsv`；
- **C++ 侧**：`path_rules_test` 用相对路径读同一文件（CI cwd 固定为 `container/`）。

### 3.4 签名的最小改动（不改语义）

```cpp
// core/path_rules.h（纯）
namespace nextvm::path {
using RuleTable = std::unordered_map<std::string, std::string>;

std::string trim_trailing_slashes(const std::string& s);
bool is_path_hidden(const std::unordered_set<std::string>& hidden, const std::string& path);
std::string redirect_path(const RuleTable& rules, const std::string& path);  // 空串 = 未命中
std::string synthetic_apk_path(const std::string& pkg);
}
```

`native-hook.cpp` 里原来的 `static std::string redirect_path(const char* path)` 改为薄包装：

```cpp
static std::string redirect_path(const char* path) {
    return nextvm::path::redirect_path(g_path_redirects, path ? path : "");
}
```

**这样 `hooked_*` 的调用点一行不用改**，风险被压到最小。

### 3.5 CI 位置（不新增 workflow）

在 `module-test.yml` 的 "Run module unit tests" **之前**加一步（ubuntu-latest 自带 g++）：

```yaml
- name: Run C++ core tests
  if: always()
  working-directory: container
  run: |
    set -euo pipefail
    g++ -std=c++17 -Wall -Wextra -Werror=return-type -O1 \
        -I core/hook/src/main/cpp/core \
        core/hook/src/main/cpp/core/path_rules.cpp \
        core/hook/src/test/cpp/path_rules_test.cpp \
        -o /tmp/path_rules_test
    /tmp/path_rules_test core/hook/src/test/resources/golden/path_vectors.tsv
```

遵守"一次一个 CI"：C++ 测试挂在现有 `module-test` 里，不额外 dispatch 第二个 workflow。

## 四、S4：设备内自检（同一份向量，真实环境）

- 开关：`debug.nextvm.pathSelfCheck=1`（新 DebugFlags 项，**release 无开销**）；
- 时机：guest 进程 `initNativeHooks` 完成、规则注册之后；
- 行为：读同一份向量（或内置精简版），跑 `redirect_path`，把 `fail` 项写 `DebugTrace.event("pathSelfCheck", ...)`；
- 价值：S1 只能证明"纯逻辑对"，S4 证明"在真实 linker/JNI/libc 下也对"。

## 五、验收判据

| 项 | 判据 |
| --- | --- |
| 语义未变 | 抽取前后 `redirect_path` 输出一致：由 TSV 向量同时约束 Java 与 C++ |
| 依赖隔离 | `core/path_rules.h/.cpp` 不 include `jni.h`/`android/*`/`link.h`/`sys/*` |
| 构建隔离 | Android CMake 不含 `src/test/cpp`；CI 的 g++ 步骤不产出 Android 产物 |
| 覆盖范围 | 至少：ROOT_FORM、兄弟包、无尾斜杠变体、隐藏精确匹配、maps 过滤、合成路径模板 |
| 不破坏日志 | 测试代码不写 Android 日志；S4 只走 `DebugTrace` |

## 六、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 抽函数时改了语义 | 先加向量与测试（对旧实现跑绿），再搬代码，搬完对同一向量再跑（顺序与规则 10 一致） |
| 2 | C++ 测试在 CI 需要额外编译时间 | 只编 2 个 TU（`path_rules.cpp` + test），秒级 |
| 3 | 向量表变成"第二份规格"难以维护 | 向量只描述**行为**，不描述实现；新增族时 `PathKinds` 与 TSV 同提交 |
| 4 | 与 B2 拆包冲突（都在动 native-hook.cpp） | **顺序固定**：先 S1（只加薄包装）→ 再做 B2 拆包；或合并为同一批（门禁只允许一次一个） |

## 七、建议的落地顺序

```
（当前）:core:hook CI 绿
  → S1-1：新增 core/path_rules.{h,cpp}（纯实现，先不改 native-hook.cpp）
  → S1-2：加薄包装（native-hook.cpp 只改 redirect_path/is_path_hidden 两处调用）
  → S1-3：加 TSV 向量 + C++ 测试 + Kotlin 的 PathGoldenTest（同一份文件）
  → 一次 module-test（:core:hook）+ 观察 C++ 步骤
  → 绿后：S4（设备自检）
  → 之后：B2 拆包（native-hook.cpp 分文件）——此时已有 C++ 回归网
```

**S1 与 S2 的分界**：S1 只覆盖"纯逻辑"；`hooked_open` 这类需要 `real_*` 的分支留到 S2（B2 拆包时）。
