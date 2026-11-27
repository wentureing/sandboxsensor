# 变更：N2-A —— 库过滤判定表下沉（只抽判据，计数与日志不变；2026-11-27）

- 目标：把 `patch_library_got` 里"哪些库该被 GOT patch"的四级判据搬到 `core/lib_filter.{h,cpp}`，
  用 host 向量固定住（这块历史上是渲染崩溃 / popup 闪退的元凶，此前**零测试**）。
- **本批边界（用户 2026-11-27 明确）**：只做 A —— **计数一个字节不改、日志文本一字不改**、过滤位置不前移。
  B 批（新增 `skipped_graphics`/`skipped_relro` 计数、把过滤前移到 `install_hooks_callback` 并修正 `libs_patched` 语义）
  另立变更文档，**A 通过测试后**才做；A、B 各自跑一次容器编译 CI。
- 关联：`架构-hook实现现状-代码为准.md` §四（四级判据与事故史）、`代码镜像/core/hook/cpp/core/lib_filter.md`

---

## 一、影响范围

| 文件 | 类型 | 改动 |
| --- | --- | --- |
| `core/hook/src/main/cpp/core/lib_filter.h` | 新增 | `Decision`/`Reason`/`Verdict` + `classify_lib` + `reason_name` |
| `core/hook/src/main/cpp/core/lib_filter.cpp` | 新增 | 四级判据（关键词与顺序逐字照搬） |
| `core/hook/src/main/cpp/native-hook.cpp` | 修改 | `patch_library_got` 的过滤段改为 `classify_lib()` + 原日志文本；**计数/后续流程不动** |
| `core/hook/src/main/cpp/CMakeLists.txt` | 修改 | 加 `core/lib_filter.cpp` |
| `core/hook/src/test/cpp/lib_filter_test.cpp` | 新增 | 向量 + 4 条不变量 |
| `core/hook/src/test/resources/golden/lib_filter_vectors.tsv` | 新增 | **20 条**向量（库名 → decision/reason） |
| `.github/workflows/module-test.yml` | 修改 | `cpp-core-test` 增编 `lib_filter.cpp` + `lib_filter_test.cpp`，增跑向量 |

**不动**：`install_hooks_callback`（self/linker/vdso/libart/libhwbinder/CM `libDexHelper` 特例、v87/v89 窗口副作用）、
`NativeHookStats` 的 9 个字段、`LOGI` 文案、`g_patched_bases` 语义、`NS_HOOK_SYSTEM_LIBS` 宏本身。

---

## 二、逐文件 diff 预览

```diff
--- native-hook.cpp（patch_library_got）
-    if (lib_name != nullptr) {
-        const bool is_self = strstr(lib_name,"com.nextvm.app") || strstr(lib_name,"libnextvm");
-        if (is_self) { LOGI("GOT patch skipped (container self): %s", lib_name); return; }
-        const bool is_graphics = strstr(…"libGLES") || … || strstr(…"libllvm-glnext");
-        if (is_graphics) { LOGI("GOT patch skipped (graphics stack): %s", lib_name); return; }
-        const bool is_app_lib = strstr(…"/data/app/") || strstr(…"/data/user/") || strstr(…"/virtual/");
-        if (!is_app_lib) {
-#if NS_HOOK_SYSTEM_LIBS
-#else
-            LOGI("GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0): %s", lib_name);
-            return;
-#endif
-        }
+    if (lib_name != nullptr) {
+        const nextvm::libfilter::Verdict verdict =
+            nextvm::libfilter::classify_lib(lib_name, NS_HOOK_SYSTEM_LIBS != 0);
+        switch (verdict.reason) {
+            case nextvm::libfilter::Reason::kContainerSelf:
+                LOGI("GOT patch skipped (container self): %s", lib_name); break;
+            case nextvm::libfilter::Reason::kGraphicsStack:
+                LOGI("GOT patch skipped (graphics stack): %s", lib_name); break;
+            case nextvm::libfilter::Reason::kSystemLib:
+                if (verdict.decision == nextvm::libfilter::Decision::kSkip)
+                    LOGI("GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0): %s", lib_name);
+                break;
+            default: break;
+        }
+        if (verdict.decision == nextvm::libfilter::Decision::kSkip) return;
     }
```

---

## 三、等价性论证（逐分支）

| 输入 | 原行为 | 新行为 |
| --- | --- | --- |
| `lib_name == nullptr` | 跳过整段过滤，继续 patch | `{kPatch, kNone}` → `switch` default → 不 return ✓ |
| 含 `com.nextvm.app` 或 `libnextvm` | LOGI(self) + return | `kContainerSelf` → 同 LOGI + return ✓ |
| 含 6 个图形栈关键词之一 | LOGI(graphics) + return | `kGraphicsStack` → 同 LOGI + return ✓ |
| 含 `/data/app/`、`/data/user/`、`/virtual/` | fallthrough 继续 patch | `{kPatch, kAppLib}` → default → 继续 ✓ |
| 其余 + `NS_HOOK_SYSTEM_LIBS=0` | LOGI(system) + return | `{kSkip, kSystemLib}` → 同 LOGI + return ✓ |
| 其余 + `NS_HOOK_SYSTEM_LIBS=1` | 无日志，继续 patch | `{kPatch, kSystemLib}` → 不打日志 → 继续 ✓ |

计数侧：`libs_scanned/libs_patched/skipped_self/skipped_system` 全部在 `install_hooks_callback` 里，
本批**没有触碰**，因此 `skipped_self=1`、`skipped_system=3` 与恒等式 `patched = libs − self − system` 都不变。

---

## 四、测试（针对"判据顺序"和"事故记忆"）

| 类型 | 内容 |
| --- | --- |
| 向量 20 条 | self（4 种形态，含"宿主库同时在 /data/app/ 下"）/ graphics（6 个关键词 + 一条"宏=1 也不能 patch 图形栈"）/ app_lib（3 种路径）/ system_lib（`/system`、`/vendor`、`/apex`、无路径的 `libDexHelper.so`、宏=0/1 两种）/ `<null>` |
| 不变量 4 条 | `nullptr` 照常 patch；**self 优先于 app_lib**；**graphics 优先于系统库开关**；空串（≠nullptr）走完整判据落到 `system_lib` |

`lib_filter_vectors.tsv` 里 `<null>` 表示 C 的 `nullptr`（与空字符串区分）—— 这是本次新增的记号，写在这里以免后人误解。

---

## 五、风险

| 风险 | 对策 |
| --- | --- |
| 判据顺序被改 → 容器自身被 patch（最坏：重定向实现拦自己） | 不变量 `self_beats_app_lib` 固定顺序；镜像文档"约束与坑 2"写明理由 |
| 图形栈被 patch → RenderThread SIGABRT 复发 | 不变量 `graphics_beats_system_toggle`；六条关键词逐字照搬 |
| 抽取时把日志文本改了，排查断链 | 日志全部留在门面，文本一字未改（deviation #4） |
| CMake 漏登记 → `undefined symbol` | 已加；`container-build` 作为该风险的门禁 |

---

## 六、验证（A 批单独跑两个 CI）

```powershell
node tools/lint/check-jni-contract.mjs      # 期望 24 ↔ 29（JNI 面未动）
node tools/analyze/verify-deleted-suffix.mjs --strict

# 1) 单测 + host 向量
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:hook" test_filter="com.nextvm.core.hook.*"
# 2) A 批单独的容器编译（CMake 登记 / NDK 编译）
node tools/release/gh-api.mjs --dispatch container-build.yml main ref="baseline-v150"
```

| 判据 | 期望 |
| --- | --- |
| `cpp-core-test` | `lib_filter_test` **20 向量 + 4 不变量全过**；`path_spoof_test` / `path_rules_test` / `path_pattern_test` 不回退 |
| `container-build` | 编译通过（`core/lib_filter.cpp` 进 `CMakeLists`） |
| 真机（可选，B 批前不必） | `[Dbg/native] hook stats` 的 `libs=415 patched=411 skipped_self=1 skipped_system=3` **完全不变** |

---

## 七、回退

```powershell
git -C _ref/ShitJumpingVM revert <N2-A commit>
```

新文件删除 + `patch_library_got` 恢复原 if 链 + CMakeLists 回退。

---

## 八、实际变更

（改完补 `git diff --stat` 与 `20261127-N2a-lib-filter.actual.diff`）

## 八之二、A 批真机验证（2026-11-27，设备 `10AFA8169Z0039G`）

装机方式：`adb install -r`（**覆盖安装**，保留沙箱数据与 appops，不动两个既有 guest 实例）。
APK：`container-shitjumpingvm-323ec41.apk`（22291118 B，versionCode 137，`versionName=1.0.0-323ec41`）。

| 判据 | 实测 | 结论 |
| --- | --- | --- |
| 版本 / appops | `1.0.0-323ec41`，`MANAGE_EXTERNAL_STORAGE: allow` | 覆盖安装未丢授权 |
| 容器就绪 | 10s 后私有目录可读；实例 `mark.via_…` / `com.sandbox.probe_…` 都在 | 数据未清 |
| `SELF_CHECK` | `ok=8 fail=0 S1..S8 全 ok` | 自检通过 |
| via 启动 | `bind complete (framework path)`，到 `onActivityResumed` | 启动链正常 |
| **`hook stats`（关键）** | 三拍全同：`libs=415 patched=411 skipped_self=1 skipped_system=3 late_passes=0 late_libs=0 spoof=0 maps_lines=0 maps_hidden=0` | **与基线逐字段一致 ⇒ N2-A 等价重构得证** |
| 探针 `suite=all` | `entries=106 {ok=93, fail=13}`，与重构前**完全相同**（fail 仍是 `P25/P26` + N 系列首轮早期窗口） | native 路径 hook 无退化 |

> 说明：A 的产物仍只有 9 个 key（N2-B 的 3 个新字段不在这个 APK 里）；B 的容器编译单独跑一次。
> 因为 `skipped_self/system` 与 `patched` 一字未变，`现状-native-关键过程基线.md` 的判据表**无需改动**。

## 九、下一批（B，等 A 绿）

| 项 | 内容 |
| --- | --- |
| 计数 | `NativeHookStats` 加 `skipped_graphics` / `skipped_relro`（`nativeGetHookStats` 输出加两个字段） |
| 前移 | `install_hooks_callback` 的早期过滤也走 `classify_lib`，并修正 `libs_patched` 语义（只在真正 patch 后 +1） |
| 基线 | 重记 `现状-native-关键过程基线.md` 的字段表与量级（`patched` 会变小） |
| 门禁 | 单独一次 `module-test` + **单独一次** `container-build`（与 A 分开） |
