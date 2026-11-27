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
| `core/hook/src/test/resources/golden/path_spoof_vectors.tsv` | 新增 | **33 条**向量（合成路径 4 / 五类改写 13 / maps 可见性 5 / maps 行 7 / 等价族 4） |
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
| 抽取时改了语义 | 逐字搬迁 + `path_spoof_vectors.tsv` **33 条**向量（含 `(deleted)` 后缀、split 名、host_fallback、两条 unchanged）+ 5 条结构不变量 |
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

### CI 第二跑（run `45931083615`）：`path_spoof_test` 25/26

```
path_rules_test: 16/16 passed
path_pattern: 21 checks, 0 failures
[FAIL] rewrite pkg=mark.via
        input=/data/user/0/com.nextvm.app/files/virtual/apks/mark.via_1.apk (deleted)
     expected=/data/app/~~VmK8x2==/mark.via-VmK8x2==/base.apk (deleted)
       actual=/data/app/~~VmK8x2==/mark.via-VmK8x2==/base.apk
path_spoof_test: 25/26 passed
```

**分类：编码错误 —— 期望值写的是"意图"，被测代码是"现状"；根因在被测代码的历史缺陷（不是本次重构引入）。**

| 判据 | 结果 |
| --- | --- |
| 回退验证 | `path_spoof.cpp` 与原 `native-hook.cpp` 该段**逐字相同**（`git show 6324660` 对照），所以不是 N1a 改了行为 |
| 根因 | `" (deleted)"` 实际 **10** 字符（空格 + 9），代码写 `size >= 11 && compare(size - 11, 11, " (deleted)")` ⇒ **剥离永不生效** |
| 后果 | 带 `(deleted)` 的 **guest apk** 路径不以 `.apk` 结尾 ⇒ 落进"含 nextvm"分支 ⇒ `host_fallback=true` ⇒ `transform_maps_line` **丢弃该行**，合成路径也丢后缀 |
| 是否有意设计 | **不是**。同一文件 `sc_rewrite_fd_target`（seccomp 归因）同样写 11 ⇒ 作者一致地把该后缀当成 11 字符，属**无意错误** |
| 严重性 | 低：只影响 maps 里"已删除的 apk mmap"行可见性 + seccomp 观测文本，**不影响隔离与读写落点** |

**处置（本批不改行为，规则 18.2：不许把顺手优化藏进重构）**：

1. `path_spoof_vectors.tsv`：该用例的 expected 改为现状（`base.apk`，无后缀）+ `flag=host_fallback`，并加注释写明这是缺陷固定点；
2. 补一条 `data` 目录的 `(deleted)` 用例（该分支不受影响，rest 自带文本），防止误以为"整类后缀都坏了"；
3. 镜像文档「偏差登记 #6」+「约束与坑 4」记下：**修它 = 行为变更（R3 类）**，且必须同时改 `sc_rewrite_fd_target`；
4. 测试**每一步都打印** `[ok] …`（用户 2026-11-27 要求：CI 日志要能看出每条用例固定了什么）。

### 缺陷溯源：**fork 之前（上游自带）**

`(deleted)` 长度用 11 的两处都不是我们写的：

| 位置（当前） | 引入提交 | 作者 / 时间 | 相对 fork 点 `63346e6`（2026-11-18，woand） |
| --- | --- | --- | --- |
| `core/path_spoof.cpp:34`（原 `native-hook.cpp` 的 `rewrite_virtual_path`） | `a1871a6` "wip: anti-detection debugging for heavily-packed guests (SecNeo case)" | **woand** / 2026-10-10 07:37 | **祖先 ✓（fork 之前）** |
| `native-hook.cpp:3943`（`sc_rewrite_fd_target`，seccomp） | `e590c32` "feat: seccomp USER_NOTIF supervision …" | **woand** / 2026-10-10 15:01 | **祖先 ✓（fork 之前）** |

验证方式（可复跑）：

```powershell
git -C _ref/ShitJumpingVM log --oneline -S 'core.size() - 11' --all -- core/hook/src/main/cpp/native-hook.cpp
git -C _ref/ShitJumpingVM log --oneline -S 'clen - 11' --all -- core/hook/src/main/cpp/native-hook.cpp
git -C _ref/ShitJumpingVM blame 63346e6 -L 320,345 -- core/hook/src/main/cpp/native-hook.cpp   # fork 点那一版就已归 woand
git -C _ref/ShitJumpingVM merge-base --is-ancestor a1871a6 63346e6   # 0 = 是祖先
```

- fork 后第一个提交是 `2cfd315`（2026-11-22，wentureing）；`git log -S` 在整个 fork 后历史里**只有 N1a 的搬运提交**（`67642d2`）碰过这两行内容 ⇒ **fork 后零次修改**。
- 数字 11 的来源几乎可以确定是 `sizeof(" (deleted)")`（= 10 字符 + `\0`）而不是 `strlen` ——
  `tools/analyze/verify-deleted-suffix.mjs` 会把两者都算出来（char=10 / sizeof=11）。

### 完善测试（同一批）

| 项 | 内容 |
| --- | --- |
| 向量从 27 → **33 条** | 新增：lib+`(deleted)`（证明 lib 分支不依赖剥离）、maps 里带 `(deleted)` 的 apk 行 → `drop`（缺陷在 maps 层的后果）、`/files/virtual/apks/*.apk` 一律映射到**当前** guest 合成路径、`/data/media/...` 真路径不动、无 `/` 的 `[anon:nextvm]` 行走关键字分支、**空行不得被丢** |
| **5 条结构不变量** | 合成 apk 必含 guest 包名 / 必不含 `nextvm`；合成 lib 形状 `/lib/arm64/<file>`；`maps_line_visible("")=true`；空路径不改写且不写 `out`；空规则表下 `/data/user/0` 前缀也不得凭空造结果 |
| **每步输出** | 每条用例与每条不变量都打 `[ok] …`，失败打 `[FAIL]` + expected/actual 四行 |
| **CI 证据步骤** | `node tools/analyze/verify-deleted-suffix.mjs --root ../container`：把字符数与 `compare(size-N, N, …)` 的成立情况算给 CI 看（信息性；`--strict` 留给修复后防回退） |
