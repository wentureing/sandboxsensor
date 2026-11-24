# 调查：`AntiDetectionEngine` 与 `ensureWebViewResources` 的来历、设计与当前阶段

- 日期：2026-11-24
- 起因：用户要求 —— ①调查这两个的设计功能与编写时期 ②现在到了什么阶段，适合检查什么

---

## 一、编写时期：**两者都是上游 v156 引入的**

```
$ git log -S "class AntiDetectionEngine" --all
63346e6  v156: trace the login page load window ... (2026-11-18 08:37)

$ git log -S "ensureWebViewResources" --all
23b0538  fix(webview): 接线 ensureWebViewResources ...   ← 我们刚加的接线
63346e6  v156: ... (2026-11-18 08:37)                     ← 首次引入
```

**`63346e6` 就是我们的 fork 点** → **两者都是上游 NEXTVM 的代码，不是我们加的**。

**编写时期：2026-11-18（v156），距现在 6 天。**

---

## 二、`AntiDetectionEngine`：**完整实现了 8 项，但从未启用**

### 2.1 规模

| 项 | 数量 |
| --- | --- |
| 文件行数 | **805 行** |
| 公共/私有方法 | **29 个** |
| `hidePath` 调用 | 19 处 |
| `SELinux` 相关 | 23 处 |
| `signature` 相关 | 39 处 |
| `DEX` 相关 | 14 处 |
| `SafetyNet`/`PlayIntegrity` | 5 处 |

### 2.2 8 项设计 vs 实现（**全部实现**）

| # | 设计项（类头注释原文） | 实现方法 | 状态 |
| --- | --- | --- | --- |
| 1 | Root detection bypass — hides su, Magisk, root packages | `hookRootChecks()` L235 | ✅ |
| 2 | Emulator detection bypass — spoofs Build properties, hides qemu files | `hookEmulatorChecks()` L279 | ✅ |
| 3 | Virtual environment detection — cleans stack traces, hides host package | `hookVirtualEnvChecks()` L332 | ✅ |
| 4 | Xposed/hook framework detection | `hookXposedChecks()` L390 | ✅ |
| 5 | SELinux context handling — reports enforcing mode | `spoofSELinuxContext()` L586 + `checkSELinuxEnforcing()` L717 | ✅ |
| 6 | APK signature verification — returns original signatures | `registerOriginalSignatures`/`spoofSignatures`/`applySignatureSpoof` | ✅ |
| 7 | DEX integrity check bypass | `hookDexIntegrityChecks()` L534 | ✅ |
| 8 | SafetyNet/Play Integrity | `hookPlayIntegrity()` L552 | ✅ |

**额外还有**：
- `hookStackTraceCleanup()` L490 + `cleanStackTrace()` L505 + `cleanException()` L517
- `hookClassLoaderForFrameworkDetection()` L614
- **`runSelfTest()` L647** ← **自带自检**

### 2.3 唯一的"总开关"：`enableAntiDetection(instanceId, level)`

**全部 8 项的调用链**：

```
enableAntiDetection(instanceId, level)  ← L161，唯一入口
  ├─ L167 hookRootChecks()
  ├─ L168 hookEmulatorChecks()
  ├─ L171 hookVirtualEnvChecks()          (level >= MODERATE)
  ├─ L172 hookXposedChecks()              (level >= MODERATE)
  ├─ L176 hookStackTraceCleanup()         (level >= AGGRESSIVE)
  ├─ L177 hookDexIntegrityChecks()        (level >= AGGRESSIVE)
  └─ L178 hookPlayIntegrity()             (level >= AGGRESSIVE)
        └─ L559 spoofSELinuxContext()     (在 hookPlayIntegrity 内)
        └─ L411 hookClassLoaderForFrameworkDetection()  (在 spoofSignatures 内)
```

**验证**：每个 `hook*` 方法都**只有 1 个调用点**，全在 `enableAntiDetection` 或其下游。

### 2.4 而 `enableAntiDetection` 本身：**零调用**

```
$ grep -r "enableAntiDetection" --include=*.kt
AntiDetectionEngine.kt:161  fun enableAntiDetection(...)   ← 只有定义
```

**所以**：
> **`AntiDetectionEngine` 是一台"装好了但没插电的机器"：**
> **805 行、8 项检测对抗、自带自检，只需一行 `enableAntiDetection(instanceId)` 就能全开。**

### 2.5 `runSelfTest()` 恰好是 SelfCheckRunner 需要的东西

```kotlin
fun runSelfTest(): AntiDetectionReport {
    val rootBinariesVisible = ROOT_BINARIES.filter { File(it).exists() }
    val emulatorPathsVisible = EMULATOR_PATHS.filter { File(it).exists() }
    val hookClassesLoadable = HOOK_FRAMEWORK_CLASSES.filter {
        try { Class.forName(it, false, ClassLoader.getSystemClassLoader()); true }
        catch (_: ClassNotFoundException) { false }
    }
    val report = AntiDetectionReport(
        rootBinariesVisible, emulatorPathsVisible, hookClassesLoadable,
        buildFingerprint = Build.FINGERPRINT, buildTags = Build.TAGS, buildType = Build.TYPE,
        isDebugBuild = ...,
        selinuxEnforcing = checkSELinuxEnforcing(),
        totalHiddenPaths = nativeHookBridge.getHiddenPathCount(),      // ← 已有！
        totalRedirections = nativeHookBridge.getRedirectionCount(),    // ← 已有！
        nativeHooksAvailable = nativeHookBridge.isNativeAvailable()    // ← 已有！
    )
    ...
}
```

**关键**：
- **`getHiddenPathCount()` / `getRedirectionCount()` / `isNativeAvailable()` 都已经存在**（native 侧有查询 API！）
- **这正是 SelfCheckRunner 需要的"容器状态量"**
- **不需要新写 native API**

---

## 三、`ensureWebViewResources`：**实现完整，但路径清单过时**

### 3.1 设计意图（原文注释）

```kotlin
/**
 * Ensure WebView can find its resources when running inside a guest app.
 * WebView loads resources from its own APK, which may not be in our AssetManager.
 */
```

**意图完全正确**：**guest 的 AssetManager 只有 guest APK，没有 WebView 的资源**。

### 3.2 为什么"过时"

| 项 | v156 时期（2026-11-18） | 现在（vivo A15 实测） |
| --- | --- | --- |
| WebView 的 APK 形态 | 单个 `WebViewGoogle.apk` | **Trichrome 拆分**：`WebViewGoogle64.apk`（壳）+ **`TrichromeLibrary64.apk`（145 MB，资源在这里）** |
| 清单里的路径 | `/system/app/WebViewGoogle/WebViewGoogle.apk` 等 4 条 | **全都对不上**（缺 64 后缀、缺 Trichrome） |

**所以**：**"过时"是因为设备架构变了（Android 10+ 的 Trichrome 拆分），不是设计错**。

### 3.3 我们的修复（`23b0538`）

- 路径清单 4 → 11 条（4 Trichrome + 7 传统，含 64 后缀）
- `ensureWebViewResources` 改为注入**所有存在**的 APK（原来 `firstOrNull` 只取第一个）
- 在 `createResourcesForApp` 缓存写入后接线

---

## 四、现在到了什么阶段

### 4.1 已完成（本轮 + 前几轮）

| # | 项 | 提交 | 验证 |
| --- | --- | --- | --- |
| 1 | 修复双轨沙箱 | `1d3e2bb` | ✅ 实测 |
| 2 | 固定签名 | `45a6af2` | ✅ 实测 |
| 3 | P0-3 主进程不装 hook + 删豁免 | `b4e85a4`/`de50a82`/`1631541` | ✅ 实测 |
| 4 | WebView 资源接线 + Trichrome | `23b0538` | ⏳ 待验 |

### 4.2 当前阶段的准确定位

**在 `架构/架构-检测面与优先级.md` 的成熟度分级里**：

```
M0 能跑          ✅
M1 能装          ✅
M2 能自检        ✅（探针 74 项）
M3 普通应用无感   ⚠️ 差 P13/P16                ← 现在在这里
M4 有风控难察觉   ❌ 差 maps 伪造 + L6
M5 加固壳        ❌ 不可达
```

**所以**：**在 M2 与 M3 之间**，且**基础设施（签名/双轨/进程边界）已经稳了**。

### 4.3 本轮暴露的**系统性问题**

| # | 模式 | 已发现 |
| --- | --- | --- |
| **1** | **"实现了但零调用"** | **3 处**：`enableAntiDetection`(19 处 hidePath + 8 项能力)、`WebViewWatch/Warmup`(574 行)、`ensureWebViewResources` |
| 2 | **过时的硬编码清单** | `WEBVIEW_APK_PATHS`（已修）、`mAudioService` 反射字段 |
| 3 | **精确匹配 vs 前缀匹配** | `is_path_hidden` |
| 4 | **`finish-imm:idle` 打断** | Via 的 Shell 只活 1188ms |

---

## 五、适合检查什么（按收益排序）

### 5.1 **最高收益：全仓"零调用"扫描**

**为什么**：**已发现 3 处，说明这是系统性问题**（上游写了很多能力但没接线）。

**方法**：
```bash
# 对每个 Kotlin 文件里的 fun，检查是否有调用点
for f in $(find . -name "*.kt" -not -path "*/build/*"); do
  for fn in $(grep -oP '^\s*(private |internal |public )?fun \K\w+' "$f" | sort -u); do
    cnt=$(grep -r "\b$fn\b" --include=*.kt . | grep -v "fun $fn" | wc -l)
    [ "$cnt" -eq 0 ] && echo "ZERO-CALL: $f :: $fn"
  done
done
```

**预期产出**：一份"已实现但未启用"的清单。
**已知会在里面**：`enableAntiDetection`、`runSelfTest`、`ensureWebViewResources`（未修前）、`WebViewWatch.schedule`、`WebViewWarmup.schedule`

**收益判断**：
- **每个"零调用"可能是一个"免费能力"**（省开发）
- **也可能是一个"死代码"**（该删）
- **必须逐个判断，不能批量处理**（这是前几轮的教训）

### 5.2 **次高收益：接线 `AntiDetectionEngine`**

**理由**：
- **805 行、8 项能力、自带 `runSelfTest()`** —— 只用一行 `enableAntiDetection(instanceId)` 就能全开
- **但必须先验证**：`enableAntiDetection` 是否在 guest 进程调用（不能在主进程，因为主进程不装 hook）

**接线位置**：`VirtualEngine.ensureGuestApplicationBooted` 成功后（guest 进程）

**验证**：
```powershell
adb logcat -d | Select-String "Enabling anti-detection|Self-test report"
adb logcat -d | Select-String "totalHiddenPaths|totalRedirections"
```

### 5.3 **第三：探针在新架构下的基线重跑**

**为什么**：**P0-3 后主进程不装 hook + 豁免已删** → **探针的很多项行为变了**：
- `P16`（容器 dataDir 可 stat）→ 现在主进程不装 hook，但**探针在 guest 进程** → 仍应返回 `exists=true`（因为 `hidePath` 未接线）
- `N03`/`N06` → **不受影响**（探针自己的 open 仍被 hook）

**动作**：跑一次完整探针，作为新基线。

### 5.4 **第四：过时反射扫描**

**已发现**：`SvcProxyMgr: Field mAudioService not found on android.media.AudioManager`

**方法**：
```bash
# 找出所有对 framework 私有字段的反射
grep -rn "getDeclaredField(\"m" --include=*.kt . | grep -v build
grep -rn "getDeclaredMethod(\"[a-z]" --include=*.kt . | grep -v build
```

**逐个核对 AOSP 的 Android 15 版**：字段是否改名/移除。

### 5.5 **不建议现在做的**

| 项 | 为什么 |
| --- | --- |
| P0-4/P0-5（隐藏容器） | 应该**先接线 `AntiDetectionEngine`** —— 它的 `hookVirtualEnvChecks` 已经包含 `hidePath("/data/data/com.nextvm.app")`，**可能直接就是 P0-5 的实现** |
| WebView 代读 | Via 的问题还没定论（需受控测试 `WebViewProbeActivity`） |
| maps 伪造 | 难度高，收益要等 M3 达成后 |

---

## 六、一个重要的重新认识

> **我们一直在"从零实现"隐藏容器的能力，但 `AntiDetectionEngine` 已经把 8 项都写好了。**
>
> **`hookVirtualEnvChecks()`（L332）里的 3 处 `hidePath` 就是 P0-5 的核心。**
> **`hookRootChecks()`（L235）的 9 处是 Root 隐藏。**
> **`spoofSignatures` / `applySignatureSpoof` 是签名伪造。**
>
> **所以 P0-4/P0-5 的正确做法不是"新写"，而是：**
> **① 接线 `enableAntiDetection`**
> **② 修它的两个已知缺陷（`is_path_hidden` 精确匹配 + 无 `readdir` 过滤）**
> **③ 用 `runSelfTest()` 验证**

**这可能把 P0-5 从"1 天新写"降到"半天接线 + 修两个缺陷"。**

---

## 七、建议的下一步（三选一或并行）

| 选项 | 内容 | 工作量 | 收益 |
| --- | --- | --- | --- |
| **A** | **全仓"零调用"扫描** | 0.5 天 | **可能发现更多免费能力** |
| **B** | **接线 `AntiDetectionEngine` + 用 `runSelfTest` 验证** | 0.5 天 | **8 项能力全开 + 自检** |
| **C** | 探针基线重跑（P0-3 后） | 0.5 天 | 新基线，为后续对比 |

**我建议 A → B 顺序**：先扫出全貌（避免遗漏其他"零调用"），再接线（一次开 8 项）。
