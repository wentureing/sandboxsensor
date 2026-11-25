# 变更计划：方案A —— 宿主 external 兜底重定向

- 日期：2026-11-25
- 分支：`baseline-v150`（**不是 main**，这是基线分支上的实验性修复）
- 设计依据：`docs/设计/设计-按进程区分的宿主路径兜底.md`
- 缺陷记录：`docs/输出/记录/记录-via离线保存路径不一致.md`、`记录-via读到宿主路径的根因.md`
- 用户要求：**实施 + CI，commit 标注明显，随时准备回退**

---

## 一、目标（一句话）

让 guest 用 `getOpPackageName()`（= 宿主包名）拼出的 external 路径落进沙箱，
从而修复 via「保存成功但离线列表看不到」。

---

## 二、影响范围

| 文件 | 改动类型 | 说明 |
| --- | --- | --- |
| `core/hook/src/main/java/com/nextvm/core/hook/NativeHookBridge.kt` | 修改（+21 行） | companion 加 3 个常量；`setupAppRedirections` 末尾加一段条件注册 |

**只改 1 个文件，不动 Java 侧、不动 native。**

### 为什么不用改 `VirtualContext`

`VirtualContext.getExternalFilesDir()` **已经**返回沙箱路径
（`sandboxManager.getExternalAppFilesDir(...)`）。
逃逸发生在"用 `getOpPackageName()` 自己拼路径"的代码里，那是 **native `open()` 层**，
所以**正确的兜底位置是路径重定向规则**，不是改 `VirtualContext`。

---

## 三、逐文件 diff

```diff
 class NativeHookBridge @Inject constructor() {
     companion object {
+
+        /** 方案A 开关：宿主 external 兜底重定向。置 false 即回退。 */
+        const val HOST_EXTERNAL_FALLBACK = true
+
+        /** 容器包名（宿主）的 external 路径前缀 —— via 用 getOpPackageName() 拼出来的就是它。 */
+        const val HOST_EXT_PREFIX_A = "/storage/emulated/0/Android/data/com.nextvm.app/"
+        const val HOST_EXT_PREFIX_B = "/sdcard/Android/data/com.nextvm.app/"
         private const val TAG = "NativeHook"
```

```diff
             "$sandboxDataDir/obb/"
         )
 
+        // 方案A：宿主 external 兜底。说明见 docs/设计/设计-按进程区分的宿主路径兜底.md
+        if (HOST_EXTERNAL_FALLBACK) {
+            val hostExtDir = "$sandboxDataDir/host_ext/"
+            addPathRedirection(HOST_EXT_PREFIX_A, hostExtDir)
+            addPathRedirection(HOST_EXT_PREFIX_B, hostExtDir)
+            runCatching { java.io.File("$sandboxDataDir/host_ext").mkdirs() }
+            Timber.tag(TAG).i(
+                "Host external fallback ON: %s|%s -> %s",
+                HOST_EXT_PREFIX_A, HOST_EXT_PREFIX_B, hostExtDir
+            )
+        }
+
         Timber.tag(TAG).d("App redirections set for $guestPackageName ($instanceId)")
```

---

## 四、关键前提（已核实，非假设）

| # | 前提 | 证据 |
| --- | --- | --- |
| 1 | **规则只在 guest 进程生效** | `initNativeHooks` 开头：`if (procName == hostPkg) { skip; return false }`（P0-3） |
| 2 | **规则表是进程级** | `NativeHookBridge` per-process @Singleton；`g_path_redirects` 是进程内静态 map |
| 3 | **最长匹配优先** | native `redirect_path`：`redirect.first.length() > best_from.length()`；Kotlin `translatePath` 同样 |
| 4 | **容器 external 无真实内容** | 宿主 `.../data/com.nextvm.app/files/` 下 8 个目录 `find -type f` 为空 |
| 5 | **`host_ext` 与既有规则不冲突** | 既有规则最长为 `/storage/emulated/0/Android/data/<guestPkg>/`；新规则前缀更长 → 胜出 |

**前提 3 特别重要**：通用规则 `/storage/emulated/0/` 也会匹配新路径，但**最长匹配优先**保证
新规则生效。

---

## 五、风险

| # | 风险 | 概率 | 缓解 |
| --- | --- | --- | --- |
| 1 | guest 需要读**容器真实** external 时被挡 | 低（前提 4 实测为空） | 开关 `HOST_EXTERNAL_FALLBACK = false` 即回退 |
| 2 | `host_ext` 目录未建导致写失败 | 低 | 已 `mkdirs()` 预建 + `runCatching` |
| 3 | 与 `pathFidelity` 模式交互 | 未知 | 本轮只测普通模式；fidelity 模式单独验 |
| 4 | 容器主界面导入 APK（从 `/sdcard/Download`）受影响 | **不应有**（主进程不装 hook） | CI 后实测导入一次 |
| 5 | 多 guest 同时跑互相干扰 | **不应有**（进程隔离） | 实测装 via + 探针同时跑 |

---

## 六、回退方案（三选一，从轻到重）

| 方式 | 命令 | 影响 |
| --- | --- | --- |
| **① 开关关闭（最快，改 1 行）** | `HOST_EXTERNAL_FALLBACK = false` → 重新构建 | 行为等价于改动前 |
| **② revert 提交** | `git -C _ref/ShitJumpingVM revert <sha>` | 完整回到改动前 |
| **③ 重置分支** | `git -C _ref/ShitJumpingVM reset --hard <改动前的 sha>` | 丢弃本次及之后所有提交 |

**提交前的 sha 会记在提交消息里**，便于 ②/③ 定位。

---

## 七、验证方法

### 7.1 构建期（CI）

| # | 判据 |
| --- | --- |
| 1 | `container-build` 成功 |
| 2 | 签名与前次一致（`apksigner verify --print-certs` 的 SHA-256 相同） |
| 3 | 构建日志里能看到新规则注册（如有 `Path redirect` 日志） |

### 7.2 设备期（装包后）

| # | 判据 | 命令 |
| --- | --- | --- |
| 1 | **规则确实注册** | `logcat -d \| Select-String "Host external fallback ON"` |
| 2 | **规则条数 +2** | `logcat -d \| Select-String "v142-redirect registered rules="` → 从 11 变 13 |
| 3 | **宿主不再产生新目录** | 保存后 `ls /storage/emulated/0/Android/data/com.nextvm.app/files/offline/` 仍空 |
| 4 | **沙箱 host_ext 有文件** | `ls <instance>/host_ext/` 非空 |
| 5 | **via 离线列表能看到条目** | 用户目视 |
| 6 | **容器导入 APK 仍可用** | `INSTALL --es path /data/local/tmp/probe-v122.apk` → Success |

### 7.3 失败判据（出现任一即回退）

- 容器主界面异常 / 无法导入 APK
- guest 启动不了（`BootCrumb` 卡在 `bind complete` 之前）
- via 行为比改动前更差

---

## 八、实际变更

见同目录 `20261125-planA-host-external-fallback.diff`（`git diff` 生成，改动后落盘）。
