# 变更：修复 `(deleted)` 后缀剥离（11 → 10）+ 结构约束与回归测试（2026-11-27）

- 目标：修掉 `rewrite_virtual_path` 与 `sc_rewrite_fd_target` 两处 `" (deleted)"` 剥离永不生效的缺陷，
  并把"长度只能来自常量"变成**编译期/静态检查**能守住的约束，附针对性测试。
- 类型：**行为变更**（规则 18.4「行为偏移（应当变的）」），因此**不并入 N1a 重构提交**，单独一批。
- 缺陷溯源：`a1871a6`（woand / 2026-10-10，`rewrite_virtual_path`）与 `e590c32`（woand / 2026-10-10，
  seccomp）—— 都在 fork 点 `63346e6` 之前，fork 后零次修改。详见 `20261127-N1a-path-spoof.md` §八。
- 实证：`node tools/analyze/verify-deleted-suffix.mjs` → `char=10 / sizeof=11`，两处源码 `N=11` 判定"不成立"。

---

## 一、问题（一句话）

`" (deleted)"` 是 **10** 个字符（空格 + 9），代码按 **11** 比较（很可能把 `sizeof` 当成了 `strlen`）
⇒ 后缀剥离永不生效 ⇒ 带 `(deleted)` 的 **guest apk** 路径不满足 `.apk` 结尾，落进"含 nextvm"分支
⇒ `host_fallback=true` ⇒ **maps 里整行被丢弃**，readlink 兜底丢后缀。

---

## 二、影响范围

| 文件 | 类型 | 改动 |
| --- | --- | --- |
| `core/hook/src/main/cpp/core/path_spoof.h` | 修改 | 新增 `kDeletedSuffix[]` + `kDeletedSuffixLen`（`sizeof - 1`，**不再手数**） |
| `core/hook/src/main/cpp/core/path_spoof.cpp` | 修改 | 剥离改用常量 |
| `core/hook/src/main/cpp/native-hook.cpp` | 修改 | `sc_rewrite_fd_target`（SIGSYS 路径）同步改用常量；`emit(" (deleted)")` 也走常量 |
| `core/hook/src/test/resources/golden/path_spoof_vectors.tsv` | 修改 | apk+`(deleted)` 期望改回"保留后缀"、去掉 `host_fallback`；maps 层同一行改为"改写"而非 `drop`；新增 seccomp 同源路径的 host 侧等价用例 |
| `core/hook/src/test/cpp/path_spoof_test.cpp` | 修改 | 新增**针对性不变量**：`kDeletedSuffixLen == strlen(kDeletedSuffix)`、剥离生效且后缀出现在输出里 |
| `tools/analyze/verify-deleted-suffix.mjs` | 修改 | 识别常量形式（`kDeletedSuffixLen`）、新增"常量定义行必须是 `sizeof(...) - 1`"检查、`--strict` 可用于 CI 防回退 |
| `.github/workflows/module-test.yml` | 修改 | 该证据步骤加 `--strict`（修复后必须绿；再写死数字就红） |

---

## 三、逐文件 diff 预览

```diff
--- core/path_spoof.h
+/** maps 行/路径里的 " (deleted)" 后缀（含前导空格）。长度一律用 kDeletedSuffixLen，**不要手数**。 */
+inline constexpr char kDeletedSuffix[] = " (deleted)";
+inline constexpr std::size_t kDeletedSuffixLen = sizeof(kDeletedSuffix) - 1;

--- core/path_spoof.cpp
-    if (core.size() >= 11 &&
-        core.compare(core.size() - 11, 11, " (deleted)") == 0) {
-        core.erase(core.size() - 11);
-        suffix = " (deleted)";
+    if (core.size() >= kDeletedSuffixLen &&
+        core.compare(core.size() - kDeletedSuffixLen, kDeletedSuffixLen, kDeletedSuffix) == 0) {
+        core.erase(core.size() - kDeletedSuffixLen);
+        suffix = kDeletedSuffix;

--- native-hook.cpp（sc_rewrite_fd_target，SIGSYS handler）
-    if (clen >= 11 && sc_streq(core + clen - 11, " (deleted)")) {
-        core[clen - 11] = 0;
-        clen -= 11;
+    constexpr int kDel = static_cast<int>(nextvm::spoof::kDeletedSuffixLen);
+    if (clen >= kDel && sc_streq(core + clen - kDel, nextvm::spoof::kDeletedSuffix)) {
+        core[clen - kDel] = 0;
+        clen -= kDel;
...
-    if (deleted) emit(" (deleted)");
+    if (deleted) emit(nextvm::spoof::kDeletedSuffix);
```

---

## 四、行为变化（必须写清楚）

| 场景 | 修复前 | 修复后 |
| --- | --- | --- |
| maps 里 `…/virtual/apks/<inst>.apk (deleted)` | **整行丢弃**（被判 host_fallback） | 改写为 `/data/app/~~VmK8x2==/<pkg>-VmK8x2==/base.apk (deleted)` 并**保留** |
| `readlink` 目标带 `(deleted)` | 返回无后缀的合成 apk 路径 | 返回带 `(deleted)` 的合成路径（语义正确：确实已删除） |
| seccomp `sc_rewrite_fd_target` 输入带 `(deleted)` | 后缀丢失（且可能落到"含 nextvm"分支） | 后缀保留 |
| `maps_lines` / `maps_hidden` 计数 | — | **默认配置下不会变**：没有 native 读者时 `build_spoofed_maps` 根本不执行（`spoof=0` / `maps_lines=0`） |

**预期可观测性**：真机上**看不到差别**（本轮基线 `spoof=0 maps_lines=0`），
所以本批的判据是 host 向量 + 常量自洽断言 + 静态检查，而不是"装机前后的日志差异"。这一点要在
`现状-native-关键过程基线.md` 的判据表里保持诚实。

---

## 五、针对它的测试（用户要求"设计针对它的测试"）

| 层 | 测试 | 固定什么 |
| --- | --- | --- |
| **向量**（`path_spoof_vectors.tsv`） | `rewrite` apk+`(deleted)` → `…/base.apk (deleted)`（无 flag） | 剥离生效、后缀保留 |
| **向量** | `rewrite` lib+`(deleted)` → `…/lib/arm64/libfoo.so (deleted)` | lib 分支不依赖剥离（修复前后都成立，防回退） |
| **向量** | `rewrite` data+`(deleted)` → `/data/data/<pkg>/files/x (deleted)` | data 分支同上 |
| **向量** | `transform` maps 行 apk+`(deleted)` → **改写后整行**（不再 `drop`） | 行为变更在 maps 层的落点 |
| **不变量** | `kDeletedSuffixLen == strlen(kDeletedSuffix)` | 常量自洽（防止"改字符串不改长度"这类第二形态） |
| **不变量** | `rewrite_virtual_path(pkg, "<apk> (deleted)", out)` ⇒ `out == synthetic_apk_path(pkg) + kDeletedSuffix && !host_fallback` | **直接钉死这个缺陷**（命名 `deleted_suffix_stripped`） |
| **静态检查**（CI） | `verify-deleted-suffix.mjs --strict`：扫源码确认没有 `- 11` 之类硬编码、常量定义行必须 `sizeof(...) - 1` | 防"再有人手数" |
| **真机** | 无（本轮不可观测）；将来有 native 读者时看 `maps_hidden` 是否下降 | 记录在基线文档 |

---

## 六、风险

| 风险 | 对策 |
| --- | --- |
| 行为变更影响真实 guest | 影响面仅"带 `(deleted)` 的 apk/lib 映射行"；maps 输出**多**行而不是少行，且路径形状仍是合成 stock 形状 |
| seccomp handler 约束（无分配/无 libc） | 只用 `constexpr char[]` 与既有 `sc_streq`；不引入 `std::string` / `std::string_view` 成员调用 |
| SIGSYS 路径不可 host 测 | 用**同一常量**把两处绑定到一起；`verify` 脚本静态核对两处都引用常量 |
| 常量写进头文件被多处 include | `inline constexpr`（C++17）无 ODR 问题；`path_spoof.h` 仍不含 Android/JNI 头 |
| 修复被误当成 N1a 的一部分 | 独立 commit + 本文档；N1a 的 `(deleted)` 向量在第二批提交里改回"意图" |

---

## 七、验证方法（全部 CI）

```powershell
node tools/analyze/verify-deleted-suffix.mjs --strict         # 期望：无缺陷、常量定义合规
node tools/lint/check-jni-contract.mjs                        # 期望 24 ↔ 29（JNI 面未动）
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:hook" test_filter="com.nextvm.core.hook.*"
```

| 判据 | 期望 |
| --- | --- |
| `cpp-core-test` | `path_spoof_test` **全过**（含新增的 `deleted_suffix_stripped` 不变量） |
| `verify --strict` | 绿（两处都引用 `kDeletedSuffixLen`，常量定义为 `sizeof - 1`） |
| `module-test (:core:hook)` | 绿 |
| `container-build` | 编译通过（`path_spoof.h` 被 `native-hook.cpp` 引用） |

---

## 八、回退

```powershell
git -C _ref/ShitJumpingVM revert <本批 commit>
```

回退后 golden 需一并回到"固定缺陷现状"的版本（同一次 revert 覆盖，因为向量在同一批提交里）。

---

## 九、实际变更

`git -C _ref/ShitJumpingVM diff --stat`：

```
 core/hook/src/main/cpp/core/path_spoof.cpp                |  9 ++---
 core/hook/src/main/cpp/core/path_spoof.h                  |  6 ++++
 core/hook/src/main/cpp/native-hook.cpp                    | 10 +++---
 core/hook/src/test/cpp/path_spoof_test.cpp                | 38 ++++++++++++++++++++++
 core/hook/src/test/resources/golden/path_spoof_vectors.tsv| 12 +++----
 5 files changed, 61 insertions(+), 14 deletions(-)
```

与计划一致，无额外改动。完整 diff 见 `20261127-deleted-suffix-11to10.actual.diff`。

静态守卫（本地已跑，CI 也会跑）：

```
node tools/analyze/verify-deleted-suffix.mjs --strict
  → 字符数 = 10，sizeof = 11
  → 源码检查通过：长度来自常量、常量由 sizeof 派生   (exit=0)
```
