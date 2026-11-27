# 变更：N1a —— 路径伪装纯逻辑下沉（`core/path_spoof`，2026-11-27）

- 目标：把 `native-hook.cpp` 里**最纯、最该被测试固定**的路径伪装逻辑搬到 `core/path_spoof.{h,cpp}`，
  并把 `guest_pkg` 从全局状态改成**入参**（多实例 / 多 guest 的前置收益）。
- 计划来源：`docs/计划/计划-native-N1a-path-spoof.md`（四问判据、范围、签名、验收均已就绪）
- 适配器口径：`docs/设计/设计-native重构与临时适配器.md`（形式 A 同名转发；台账见 §四）
- 容器基线：`baseline-v150`，改前 `native-hook.cpp` **8003 行**

---

## 一、范围（一批两个职责，不超）

| 职责 | 内容 |
| --- | --- |
| ① 路径/映射合成 | `synthetic_apk_path` / `synthetic_lib_path` / `rewrite_virtual_path` / `maps_line_visible` / `transform_maps_line` |
| ② 隐藏判定与包名替换（等价族兜底） | `redirect_path` 的 `/data/user/0` ↔ `/data/data` 兜底段 |

**不动**：`hooked_*` 入口、JNI 名/签名、`.so` 名、`NS_*` 语义、`maps_line_visible` 的关键词表、
`build_spoofed_maps/status/stat` 的**读文件**部分（N1b）。

---

## 二、影响范围

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `core/hook/src/main/cpp/core/path_spoof.h` | 新增 | 6 个纯函数声明（不含 Android/JNI 头） |
| `core/hook/src/main/cpp/core/path_spoof.cpp` | 新增 | 从 `native-hook.cpp` 逐字搬出的实现，`guest_pkg` 入参 |
| `core/hook/src/main/cpp/native-hook.cpp` | 修改 | 6 处函数体改为薄转发 + 1 行 include；净 **−117 行** |
| `core/hook/src/main/cpp/CMakeLists.txt` | 修改 | 源列表加 `core/path_spoof.cpp` |
| `core/hook/src/test/cpp/path_spoof_test.cpp` | 新增 | host 向量测试（自写判定，不依赖 gtest） |
| `core/hook/src/test/resources/golden/path_spoof_vectors.tsv` | 新增 | **26 条**向量（合成路径 4 / 五类改写 9 / maps 三态 4 / maps 行 5 / 等价族 4） |
| `.github/workflows/module-test.yml` | 修改 | `cpp-core-test` job 增编 + 增跑 `path_spoof_test` |
| `docs/代码镜像/core/hook/cpp/core/path_spoof.md` | 新增 | 镜像文档四节（职责/函数/偏差登记/约束与坑） |

---

## 三、逐文件 diff 预览（门面侧）

```diff
+#include "core/path_spoof.h"

 static std::string redirect_path(const char* path) {
     if (path == nullptr || path[0] == '\0') return "";
-    std::string redirected = nextvm::path::redirect_path(g_path_redirects, std::string(path));
-    if (!redirected.empty()) return redirected;
-    if (std::strncmp(path, "/data/user/0/", 13) == 0) {
-        std::string alt_path = std::string("/data/data/") + (path + 13);
-        return nextvm::path::redirect_path(g_path_redirects, alt_path);
-    }
-    return "";
+    return nextvm::spoof::redirect_path_with_alias(g_path_redirects, std::string(path));
 }

-static bool maps_line_visible(const char* line) { …7 个关键词… }
+static bool maps_line_visible(const char* line) {
+    return nextvm::spoof::maps_line_visible(line);
+}

-static std::string synthetic_apk_path() { return "/data/app/~~VmK8x2==/" + g_spoofed_package_name + …; }
-static std::string synthetic_lib_path(const std::string& file_name) { … }
-static bool rewrite_virtual_path(const std::string& path, std::string& out, bool* host_fallback) { …~55 行… }
-static bool transform_maps_line(const char* line, std::string& out) { …~27 行… }
+static std::string synthetic_apk_path() { return nextvm::spoof::synthetic_apk_path(g_spoofed_package_name); }
+static std::string synthetic_lib_path(const std::string& file_name) { …(global, file_name)… }
+static bool rewrite_virtual_path(const std::string& path, std::string& out, bool* host_fallback) { …(global, path, out, host_fallback)… }
+static bool transform_maps_line(const char* line, std::string& out) { …(global, line, out)… }
```

**调用点一行未改**：`hooked_open/stat/readlink/fopen/...` 里的 `redirect_path(path)`（16 处）、
`build_spoofed_maps` 里的 `transform_maps_line`、readlink 改写里的 `rewrite_virtual_path` 全部照旧。

---

## 四、适配器台账（规则：每加一行都要记）

| 文件:符号 | 形式 | 为什么 | 何时删 | 怎么验证 |
| --- | --- | --- | --- | --- |
| `native-hook.cpp:redirect_path` | A 同名转发 | 绑定 `g_path_redirects`；调用点 16 处 | N1b/收尾（等所有调用点直接传规则表） | `hooked_*` 行为 + 探针 J/N 系列不退化 |
| `native-hook.cpp:maps_line_visible` | A 同名转发 | 与 `transform_maps_line` 内联调用保持同签名 | 同上 | maps 三态 golden + `maps_hidden` 量级 |
| `native-hook.cpp:synthetic_apk_path` | A 同名转发 | 绑定 `g_spoofed_package_name` | 同上 | `path_spoof_vectors.tsv` + maps 改写结果 |
| `native-hook.cpp:synthetic_lib_path` | A 同名转发 | 同上 | 同上 | 同上 |
| `native-hook.cpp:rewrite_virtual_path` | A 同名转发 | 同上；被 `transform_maps_line` 与 readlink 改写共用 | 同上 | 同上 |
| `native-hook.cpp:transform_maps_line` | A 同名转发 | 同上 | 同上 | 同上 |

**没有**使用 B（`extern "C"` 兼容符号）或 C（宏/内联切换）。

---

## 五、风险与判据

| 风险 | 对策 / 判据 |
| --- | --- |
| 抽取时改了语义 | 逐字搬迁 + `path_spoof_vectors.tsv` **26 条**向量（含 `(deleted)` 后缀、split 名、host_fallback、两条 unchanged） |
| 热路径性能 | 跨 TU 同名转发 ≈1–3 ns/次（相对 `openat` 1–2 µs 可忽略）；函数**未**新增分配/锁/日志；签名逐字保留（含返回 `std::string`） |
| 漏登记 CMake | 已加 `core/path_spoof.cpp`；`check-jni-contract` 仍绿（24↔29） |
| 多实例收益被误用 | `guest_pkg` 已是入参，但**当前只有门面在传全局值** —— 参数化是"能改"，不是"已改语义" |
| 真机回归 | `nativeGetHookStats` 的 `maps_lines`/`maps_hidden` 与 `现状-native-关键过程基线.md` 同量级；探针 N03–N09/W36 不退化 |

---

## 六、验证方法（全部走 CI，本地不编译）

```powershell
# 1) 静态（毫秒级，本地可跑）
node tools/lint/check-jni-contract.mjs            # 期望：24 ↔ 29（5 旧符号提示）

# 2) 一次 module-test（含 cpp-core-test + 编译 :core:hook）
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:hook" test_filter="com.nextvm.core.hook.*"
```

| 层 | 判据 |
| --- | --- |
| `cpp-core-test` | `path_rules_test` / `path_pattern_test` / **`path_spoof_test`（29/29）** 全过 |
| `module-test (:core:hook)` | Kotlin 单测绿 + 假绿守卫 `tests>0` |
| `container-build` | native 编译通过（新 `.cpp` 进 CMake） |
| 真机（下一轮装机时） | 探针 N03–N09/W36 不退化 + `[Dbg/native] hook stats` 的 `maps_*` 同量级 |

---

## 七、回退方案

```powershell
git -C _ref/ShitJumpingVM revert <N1a commit>
# 或（未提交时）
git -C _ref/ShitJumpingVM checkout -- core/hook/src/main/cpp/native-hook.cpp core/hook/src/main/cpp/CMakeLists.txt
rm core/hook/src/main/cpp/core/path_spoof.{h,cpp}
```

---

## 八、实际变更

见同目录 `20261127-N1a-path-spoof.actual.diff`（`git diff` + `--stat`）。
与计划的差异：**净 −117 行**（预估 −120 行），新增 6 个转发 + `path_spoof.{h,cpp}`（约 175 行实现 + 45 行声明）。

### CI 首跑（run `45930716340`）与修复

| job | 结果 |
| --- | --- |
| `module-test (:core:hook)` / `(:core:virtualization)` / `(:core:sandbox)` / `(:core:common)` | ✅ success |
| `compile-app` | ✅ success |
| **`cpp-core-test`** | ❌ **failure** |

失败信息（**分类：编码错误 —— 构建清单漏项**，规则 18.4）：

```
path_rules.cpp:(.text+0x126): undefined reference to `nextvm::path::pattern_matches(…)`
path_rules.cpp:(.text+0x135): undefined reference to `nextvm::path::pattern_is_root_form(…)`
path_rules.cpp:(.text+0x180): undefined reference to `nextvm::path::parse_pattern(…)`
collect2: error: ld returned 1 exit status
```

**回退验证**：不是行为问题 —— 报错发生在**链接期**，且同一命令里 `path_rules.cpp` 本身没改；
缺的是 `path_pattern.cpp` 这个 TU（`path_spoof_test` 的编译行只带了 `path_rules + path_spoof`）。
**修法**：workflow 该行补 `"$CORE/path_pattern.cpp"`；镜像文档「约束与坑」第 5 条同步写明这条依赖。

> 结论：N1a 的 C++ 逻辑本身没有编译错误（`path_spoof.cpp` 编过了），只在链接期暴露了构建清单遗漏。
