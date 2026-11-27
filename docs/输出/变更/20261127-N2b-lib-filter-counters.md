# 变更：N2-B —— 补库过滤的观测缺口（新增 3 个计数，行为不变；2026-11-27）

- 目标：N2-A 把判据抽成表之后，`patch_library_got` 层的三种跳过**仍然没有计数**（只能靠 `LOGI`，而真机 logcat 会被 ROM 吞）。
  本批补三个 `std::atomic<int>` 字段，让"哪一类库被跳过"变成可观测数字。
- **行为不变**：不改判据、不改日志文本、不改 `skipped_self` / `skipped_system` 的语义与数值、
  不动过滤位置、不动 `libs_patched` 语义（那属 N2-C，见 §六）。
- 前置：N2-A 已落地（`core/lib_filter.{h,cpp}` + 20 向量 + 4 不变量），A 的 `module-test` / `container-build` 各自单独跑。
- 关联：`20261127-N2a-lib-filter.md`、`现状-native-关键过程基线.md`、`架构-native模块关系与技术路线.md` §3

---

## 一、为什么需要（观测缺口的具体形态）

N2-A 之前 / 之后，`[Dbg/native] hook stats` 只有两个 skip 桶：

```
libs=415 patched=411 skipped_self=1 skipped_system=3 …
```

这两个桶只覆盖 `install_hooks_callback` 里 `libnextvm` / `linker` / `vdso` / `libart` / `libhwbinder`
五处早退。而 `patch_library_got` 里的三级跳过 —— **宿主包名下的库**、**图形栈**、**其余系统库** —— 一个数都没有。
后果：判据表改了（或 `NS_HOOK_SYSTEM_LIBS` 被打开）时，除了 `patched` 变化外**没有任何线索**指向是哪一类，
而这一类恰好是历史事故（RenderThread SIGABRT / popup 闪退 / Via 纯文本）的成因。

---

## 二、改动

| 文件 | 改动 |
| --- | --- |
| `core/hook/src/main/cpp/native-hook.cpp` | `NativeHookStats` 加 3 个字段；`patch_library_got` 的过滤 `switch` 按 `reason` 各 +1；`nativeGetHookStats` 输出加 3 个 key（`buf` 320 → 480） |

```diff
 struct NativeHookStats {
     …
     std::atomic<int> libs_skipped_system{0};
+    // N2-B：patch_library_got 层的过滤命中（此前完全看不到）
+    std::atomic<int> libs_skipped_self_host{0};   // 宿主包名下的库（com.nextvm.app）
+    std::atomic<int> libs_skipped_graphics{0};    // 图形栈（六条关键词）
+    std::atomic<int> libs_skipped_syslib{0};      // 其余系统库（NS_HOOK_SYSTEM_LIBS=0）
     …

 // patch_library_got 过滤段
         switch (verdict.reason) {
             case nextvm::libfilter::Reason::kContainerSelf:
+                g_native_stats.libs_skipped_self_host.fetch_add(1, std::memory_order_relaxed);
                 LOGI("GOT patch skipped (container self): %s", lib_name);
             case nextvm::libfilter::Reason::kGraphicsStack:
+                g_native_stats.libs_skipped_graphics.fetch_add(1, std::memory_order_relaxed);
                 LOGI("GOT patch skipped (graphics stack): %s", lib_name);
             case nextvm::libfilter::Reason::kSystemLib:
                 if (verdict.decision == nextvm::libfilter::Decision::kSkip) {
+                    g_native_stats.libs_skipped_syslib.fetch_add(1, std::memory_order_relaxed);
                     LOGI("GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0): %s", lib_name);
                 }

 // nativeGetHookStats
-             "libs=%d patched=%d skipped_self=%d skipped_system=%d "
+             "libs=%d patched=%d skipped_self=%d skipped_system=%d "
+             "skipped_self_host=%d skipped_graphics=%d skipped_syslib=%d "
```

---

## 三、新字段含义（写进基线文档）

| 字段 | 含义 | 与旧字段的关系 |
| --- | --- | --- |
| `skipped_self_host` | `patch_library_got` 里因"路径含 `com.nextvm.app`"被跳过 | **不并入** `skipped_self`（后者必须保持 1 = 只有 `libnextvm-native.so`） |
| `skipped_graphics` | 因图形栈六条关键词被跳过 | 新桶；`NS_HOOK_SYSTEM_LIBS=1` 时**仍应跳过** |
| `skipped_syslib` | `NS_HOOK_SYSTEM_LIBS=0` 下被跳过的其余系统库 | 新桶；与 `skipped_system` 不同（后者是 callback 层的 linker/vdso/art/hwbinder） |

> **红线保持不变**：`skipped_self=1`、`skipped_system=3`、`patched = libs − self − system`。
> 新增字段只是"额外的分流说明"，不改变任何既有数值。

---

## 四、风险

| 风险 | 对策 |
| --- | --- |
| 计数改变既有字段值 | 只**新增**字段，旧字段的 `fetch_add` 位置与条件一个没改（diff 可核对） |
| 热路径开销 | 每次 dl_iterate_phdr 回调至多 1 次 `relaxed` 原子加，与既有 5 处同量级；无分配、无锁、无日志 |
| 输出字符串变长 | `buf` 320 → 480；实测一行约 190 字符，远低于上限 |
| Kotlin 侧解析 | `hookStats()` 返回原始字符串，`DebugTrace.event` 只记录不分词 ✓ 无解析依赖 |

---

## 五、验证

```powershell
node tools/lint/check-jni-contract.mjs     # 期望 24 ↔ 29（签名没变，只是返回字符串内容变长）
# B 批单独两次 CI（与 A 分开，用户 2026-11-27 要求）
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:hook" test_filter="com.nextvm.core.hook.*"
node tools/release/gh-api.mjs --dispatch container-build.yml main ref="baseline-v150"
```

| 判据 | 期望 |
| --- | --- |
| `cpp-core-test` | 四个 host 测试全过（本批不动纯逻辑，仅回归） |
| `container-build` | 编译通过（`NativeHookStats` 字段与 `snprintf` 参数个数一致） |
| 真机（下次装机） | `[Dbg/native] hook stats` 出现 12 个 key；`skipped_self=1`、`skipped_system=3`、`libs=415 patched=411` **不变**；`skipped_graphics` / `skipped_syslib` / `skipped_self_host` 首次取到值 |

> 真机首取数值前，`现状-native-关键过程基线.md` 里这三个字段标"**待首取**"，不写猜测值。

---

## 六、明确不做（留 N2-C）

1. **不**把 `install_hooks_callback` 的早期过滤也接 `classify_lib` —— 那里有 v87/v89 窗口副作用与既有计数，前移会改 `skipped_*` 语义；
2. **不**修 `libs_patched` 偏松（现在包含"进了 `patch_library_got` 又被内部过滤掉"的库）；
3. **不**改 `g_patched_bases` 的插入时机（影响延迟 patch 幂等与性能）。

N2-C 若要做，必须单独变更文档 + 基线重记 + 真机复验。

---

## 七、回退

```powershell
git -C _ref/ShitJumpingVM revert <N2-B commit>
```

---

## 八、实际变更

```
 core/hook/src/main/cpp/native-hook.cpp | 16 +++++++++++++++-
 1 file changed, 15 insertions(+), 1 deletion(-)
```

与计划一致（3 个字段 + 3 处 `fetch_add` + `snprintf` 补 3 个 key 与 3 个实参 + `buf` 320→480）。
完整 diff：`20261127-N2b-lib-filter-counters.actual.diff`。
