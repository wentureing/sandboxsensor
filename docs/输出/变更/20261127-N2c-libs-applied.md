# 变更：N2-C（第 1 步）—— 把"hook 到底有没有真的打上"变成数字（2026-11-27）

- 目标：现有 `patched` 的真实含义是"通过 callback 过滤的库数"，**不是**"patch 成功的库数"
  （实测 `skipped_graphics 7 + skipped_syslib 404 + skipped_self_host 0 = 411 = patched`，即 411 个库**全部被过滤**）。
  本批新增两个硬计数，让"真的替换了几个 GOT 槽"可观测。
- 类型：**纯观测**（不改判据、不改过滤面、不改 hook 行为）。
- 前置：`20261127-N2a-lib-filter.md`、`20261127-N2b-lib-filter-counters.md`；
  `N03–N07 errno=2` + `late_passes=0` 的实证见 `现状-native-关键过程基线.md`。
- 后续：第 2 步（+5s/+20s 采样线程触发一次延迟 patch）与第 3 步（L6 触发点/白名单，仅计划）另立文档。

---

## 一、改动（4 处）

| 位置 | 改动 |
| --- | --- |
| `NativeHookStats` | 新增 `libs_applied`（成功替换过 ≥1 个 GOT 槽的库数）、`slots_patched`（槽替换总次数） |
| `patch_got_entry` | 在 `*got_addr = new_func;` 之后 `slots_patched++`（幂等分支 `*got_addr == new_func` 不计） |
| `patch_library_got` | 局部 `applied_here` 随每次成功 patch 自增；函数末尾 `>0` 则 `libs_applied++` |
| `nativeGetHookStats` | 输出加 `applied=` / `slots=` 两个 key（`buf` 480 → 640） |

```diff
+    g_native_stats.slots_patched.fetch_add(1, std::memory_order_relaxed);
...
+    int applied_here = 0;  // 本库成功替换的 GOT 槽数（N2-C 观测用）
...
+                        ++applied_here;
...
+    if (applied_here > 0) {
+        g_native_stats.libs_applied.fetch_add(1, std::memory_order_relaxed);
+    }
...
-             "libs=%d patched=%d skipped_self=%d skipped_system=%d "
+             "libs=%d patched=%d applied=%d slots=%d "
```

---

## 二、字段语义（写进基线文档）

| 字段 | 含义 | 判据 |
| --- | --- | --- |
| `applied` | **真正替换过至少一个 GOT 槽的库数** | 0 = 这个进程里一个库都没被 hook（当前 L6 缺口下的预期值） |
| `slots` | GOT 槽替换总次数（幂等重复不计） | 与 `applied` 一起看：`slots ≥ applied`；两者都为 0 就是"没 hook" |

与旧字段的关系：`libs ≥ patched ≥ applied`；`patched` 保持原义（过 callback 的库数），**不改名、不改值**。

---

## 三、预期（本批不修 L6，所以预期就是"数字难看"）

| guest | 预期 `applied` / `slots` | 说明 |
| --- | --- | --- |
| `mark.via`（纯 Java） | **0 / 0** | 它没有 native 库，本来就不该有 app 库被 patch；启动库全是系统库（被过滤） |
| `com.sandbox.probe` | **0 / 0** | `libprobe.so` 在 bind 之后 `System.loadLibrary` 加载 → 延迟 patch 未触发（L6） |

> 这两个 0 **不是回归**，而是把已知缺口量化。第 2 步（+5s 触发一次延迟 patch）后，`com.sandbox.probe`
> 的 `applied` 应变成 ≥1、`slots` 变成 ≥6（`open/openat/access/stat/lstat/readlink/fopen/...` 若干槽），
> 并且探针 `N03–N07` 应从 `errno=2` 变成 ok —— 那就是"hook 真的上了"的硬证据。

---

## 四、验证

```powershell
node tools/lint/check-jni-contract.mjs     # 期望 24 ↔ 29（签名未变，仅返回字符串变长）
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:hook" test_filter="com.nextvm.core.hook.*"
node tools/release/gh-api.mjs --dispatch container-build.yml main ref="baseline-v150"
```

真机：`install -r` 后启动 → 等 ≥10s → 取 `[Dbg/native] hook stats`，核对：
`libs=415 patched=411` 不变；新增 `applied=0 slots=0`（当前预期）。

---

## 五、风险

| 风险 | 对策 |
| --- | --- |
| 计数影响热路径 | 只在 `patch_got_entry` 真正写槽时 +1（初始化/延迟 patch 期，非每次 I/O） |
| 输出变长 | `buf` 480 → 640（一行约 210 字符） |
| 误读 `applied=0` 为回归 | 基线文档写明"0 是当前 L6 缺口的预期值，不是错误" |

---

## 六、回退

```powershell
git -C _ref/ShitJumpingVM revert <本批 commit>
```

---

## 七、实际变更

（改完补 `git diff --stat`）
