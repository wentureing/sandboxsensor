# 变更：N2-C（第 2 步）—— bind 之后主动触发一次延迟 patch（修 L6 触发链；2026-11-27）

- 目标：让"bind 之后 `System.loadLibrary` 加载的 app 库"也能被 GOT patch，把 L6 缺口补上。
- 类型：**行为变更**（会有库真的被 patch，进而影响这些库的 `open/stat/...` 语义）。
- 前置（实测证据）：`N03–N07` 全 `errno=2` + `late_passes=0` + `applied=0/slots=0`（见 `现状-native-关键过程基线.md`）。
- 与第 3 步的分工：本步**只加一个触发点**，不碰过滤面、不动白名单、不修 `Runtime.nativeLoad` hook。
  第 3 步（`Runtime.nativeLoad` 为什么没装成功 / `libnativeloader` 精确白名单）只做计划，见
  `docs/计划/计划-native-L6-触发与白名单.md`。

---

## 一、根因回顾（为什么需要这一步）

```
guest System.loadLibrary()
  → Runtime.nativeLoad → libart / libnativeloader 内部 dlopen
     ① libnativeloader.so 是系统库：NS_HOOK_SYSTEM_LIBS=0 ⇒ 不 patch ⇒ dlopen 不被 hooked_dlopen 拦
     ② 本该兜底的 Runtime.nativeLoad hook：logcat/debug-trace 里没有任何成功痕迹
     ⇒ patch_newly_loaded_libraries() 从未执行（late_passes=0 / applied=0 / slots=0）
```

上游（`a1871a6`）的机制依赖"系统库也被 patch"这个前提；我们 fork 后（`b2b485b`）为了修渲染崩溃把系统库
默认关掉，前提消失，但没补替代触发点。本步用一个**零新增线程**的方式补上。

---

## 二、改动（3 个文件）

| 文件 | 改动 |
| --- | --- |
| `core/hook/src/main/cpp/native-hook.cpp` | 新增第 **25** 个 JNI 导出 `nativePatchNewlyLoaded()`：`g_initialized` 为真时调一次 `patch_newly_loaded_libraries()` |
| `core/hook/src/main/java/com/nextvm/core/hook/NativeHookBridge.kt` | `external fun nativePatchNewlyLoaded(): Boolean` + 包装 `patchNewlyLoaded()`（异常返回 false） |
| `core/virtualization/.../lifecycle/startup/GuestDiagnosticsRegistration.kt` | 采样线程（`nv-native-stats`，+5s / +20s）里**先**触发 patch，**再**取统计；日志前缀加 `patchedNewly=` |

```diff
+JNIEXPORT jboolean JNICALL
+Java_com_nextvm_core_hook_NativeHookBridge_nativePatchNewlyLoaded(JNIEnv* env, jobject thiz)
+{
+    if (!g_initialized) return JNI_FALSE;
+    patch_newly_loaded_libraries();
+    return JNI_TRUE;
+}

+    external fun nativePatchNewlyLoaded(): Boolean
+    fun patchNewlyLoaded(): Boolean = runCatching { nativePatchNewlyLoaded() }.getOrDefault(false)

-                        DebugTrace.event("native", "hook stats($label): " + hookStats())
+                        val patched = nativeHookBridge.patchNewlyLoaded()
+                        DebugTrace.event("native", "hook stats($label): patchedNewly=$patched " + hookStats())
```

**不新增线程**：复用已有的 `nv-native-stats` 采样线程（+5s 与 +20s 各触发一次，幂等安全）。

---

## 三、契约影响

| 项 | 变化 |
| --- | --- |
| JNI 导出 | **24 → 25**（`check-jni-contract` 实测 25 ↔ 30，5 个旧符号照旧提示） |
| 已有 24 个签名 | 未改 |
| `.so` 名 / `NS_*` 语义 | 未改 |
| Kotlin `external` | +1 |

---

## 四、预期效果（判据）

| 项 | 修复前 | 修复后（预期） |
| --- | --- | --- |
| `hook stats` | `applied=0 slots=0 late_passes=0` | `com.sandbox.probe` 在 +5s/+20s 拍到 `applied ≥ 1`、`slots ≥ 6`、`late_passes ≥ 1` |
| `late_passes` / `late_libs` | 0 / 0 | ≥1 / ≥1（`late_libs` = 新增被 patch 的库数） |
| 探针 `N03–N07` | 全 `errno=2` | **应变为 ok**（`libprobe.so` 自己的 `open/stat/access/fopen/openat` 被翻译到沙箱） |
| 旧判据 | `libs=415 patched=411 skipped_self=1 skipped_system=3` | **不变** |
| via（纯 Java） | `applied=0` | 仍应为 0（它没有 native 库，本来就不该 patch）—— 这是**重要的反向判据** |

---

## 五、风险与对策

| 风险 | 对策 / 判据 |
| --- | --- |
| 被 patch 的库行为变化（它们的 `open/stat` 开始翻译） | 这正是目标；但需回归：via 渲染/popup、探针全量、`maps`/`maps_hidden` 量级 |
| 触发时机太早/太晚 | +5s 与 +20s 各一次；幂等（`g_patched_bases`）；若某库在 20s 后才加载，仍漏（留给第 3 步） |
| 采样线程被 patch 卡住 | `patch_newly_loaded_libraries` 内部有 `in_pass` 重入保护与 `g_patch_mutex`；采样线程是 daemon，卡住也不影响 UI |
| 与第 1 步判据混淆 | 第 1 步的 `applied=0` 是"修复前的现状"，第 2 步之后必须变正；两个数字一起看 |
| 加固壳 guest 回归 | 本步不针对它们；需要时单独验证（bin.mt.plus 已知 L3 崩溃与本次无关） |

---

## 六、验证

```powershell
node tools/lint/check-jni-contract.mjs        # 期望 25 ↔ 30
node tools/lint/kotlin-lint.mjs <改动的两个 kt>   # 已过
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:hook" test_filter="com.nextvm.core.hook.*"
node tools/release/gh-api.mjs --dispatch container-build.yml main ref="baseline-v150"
```

真机（按 `现状-native-关键过程基线.md` 的等待约定：启动后等 ≥10s）：
1. `hook stats` 出现 `patchedNewly=true`，`applied/slots/late_passes/late_libs` 变正；
2. 探针 `suite=all`：`N03–N07` 从 `errno=2` 变 ok；
3. `libs=415 patched=411 skipped_self=1 skipped_system=3` 不变；
4. via 能正常启动（`bind complete` + `onActivityResumed`）。

---

## 七、回退

```powershell
git -C _ref/ShitJumpingVM revert <本批 commit>
```

回退后 `applied/slots` 回到 0，`N03–N07` 回到 `errno=2`（即当前已知缺口状态）。

---

## 八、实际变更

（改完补 `git diff --stat`）
