# 设计：权限传导的配置策略 + hook/豁免规划

- 日期：2026-11-23
- 依据：P28 三方对比（`记录-P28与权限传导-完整结论.md`）+ AOSP 源码验证（`调研/记录-方向A可行性-native-loader-hook断点.md`）
- 两份内容：**① 怎么让权限"不轻易传导"** ② **基于 AOSP 的 hook 与豁免规划**

---

# 第一部分：权限传导

## 一、为什么容器申请权限会传导给 guest

**事实链**（三条，都有实测/源码支撑）：

| # | 事实 | 依据 |
| --- | --- | --- |
| 1 | guest 是容器的**子进程**（fork），**共享同一个 uid** | `P01.uid` 在容器内外都是容器的 uid |
| 2 | Android 的访问控制第一层是 **DAC（按 uid）** | `/sdcard` 文件权限、`open()` 的 `EACCES` |
| 3 | 同 uid 的进程**无法在 DAC 层区分** | 内核没有"这是 guest 还是容器"的概念 |

**所以**：容器拿到 `MANAGE_EXTERNAL_STORAGE` → guest 的 `open("/sdcard/...")` 也能过。

**实测印证**（`P28`）：

| 环境 | 容器/宿主有 `MANAGE_EXTERNAL_STORAGE` | `P28` |
| --- | --- | --- |
| 直接安装 | 无 | `EACCES` |
| Gaia | 无 | `EACCES` |
| **我们容器（vivo A15）** | **`allow`** | **能写 `/sdcard/Download/`** |

## 二、四种可能的"不传导"方案

| 方案 | 做法 | 能防什么 | 防不住什么 | 可行性 |
| --- | --- | --- | --- | --- |
| **A. 容器不申请敏感权限** | manifest 不声明 `MANAGE_EXTERNAL_STORAGE`，改用 SAF | **全部**（DAC 层就没有） | 用户真的需要整盘访问时无解 | ✅ **最彻底** |
| **B. Java 层 hook 权限查询** | hook `PackageManager.checkPermission` / `Context.checkCallingOrSelfPermission`，对 guest 返回 `DENIED` | 应用**自己做的权限检查** | **native 直接 `open`**（P28 就是这条） | ✅ 有效但有限 |
| **C. appops 层限制** | 对 guest 设 `AppOpsManager` 限制 | 系统 API 的 appops 检查 | **同 uid 无法区分**；直接 `open` 也不查 appops | ❌ 不可行 |
| **D. 每 guest 独立 uid** | fork 后换 uid | 彻底 | fork 后换 uid 需要 root；**不可行** | ❌ 不可行 |

**结论**：
> **同 uid 的子进程在 DAC 层无法隔离。**
> 想真正"不传导"，只能**不申请**（方案 A）；
> 方案 B 只能挡住"应用层自查"，挡不住 native 直接访问。

## 三、推荐的配置策略（分层）

### 3.1 权限分级表

| 权限 | 默认是否申请 | 理由 | 替代方案 |
| --- | --- | --- | --- |
| `INTERNET` | ✅ 申请 | 几乎所有应用都需要 | — |
| `ACCESS_NETWORK_STATE` / `ACCESS_WIFI_STATE` | ✅ 申请 | 无害 | — |
| **`MANAGE_EXTERNAL_STORAGE`** | ❌ **默认不申请** | **会传导给所有 guest，等于放开整盘** | **SAF（`ACTION_OPEN_DOCUMENT`）+ fd** |
| `READ/WRITE_EXTERNAL_STORAGE` | ⚠️ 按需 | 只影响 `Android/data` 和公共目录 | SAF |
| `QUERY_ALL_PACKAGES` | ⚠️ 按需 | 用于列出可克隆应用 | 只查 LAUNCHER 类别 |
| **`READ_MEDIA_IMAGES/VIDEO/AUDIO`** | ❌ 默认不申请 | 同上，会传导 | SAF |
| 相机 / 麦克风 / 位置 | ❌ 默认不申请 | guest 需要时**单独弹窗** | 运行时申请 |
| `REQUEST_INSTALL_PACKAGES` | ✅ 申请 | 装 APK 必须 | — |
| `QUERY_ALL_PACKAGES`（另用途：检测安装列表） | ⚠️ 按需 | 隐私敏感 | — |

### 3.2 三条实施要点

**① 导入口径改为 SAF，不靠全局权限**

```kotlin
/ 之前（依赖 MANAGE_EXTERNAL_STORAGE）
val apkFile = File("/sdcard/Download/x.apk")

/ 之后（SAF：只拿一个 fd，不影响其他 guest）
val uri = /* ACTION_OPEN_DOCUMENT 返回的 */ uri
contentResolver.openInputStream(uri)?.use { ins ->
    val tmp = File(cacheDir, "import.apk")
    tmp.outputStream().use { ins.copyTo(it) }
    engine.installApp(tmp.absolutePath)
}
```

**关键**：SAF 的授权是**按 URI** 的，不会变成全局权限，**不会传导给 guest**。

**② 需要整盘访问时，做成"临时 + 可撤销"**

```kotlin
/ 只在用户点"导入整个文件夹"时申请
fun requestAllFilesAccess(activity: Activity) {
    val intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
        Uri.parse("package:${activity.packageName}"))
    activity.startActivity(intent)
}
/ 用完（比如导入完成）后提示用户撤销，或记录"上次使用时间"定期提醒
```

**③ 权限使用要留痕（哪个 guest 用过什么）**

即使技术上挡不住，也要能**审计**：

```
files/probe-out/perm-audit.json
[
  { "guest": "com.sandbox.probe_xxx", "perm": "MANAGE_EXTERNAL_STORAGE",
    "firstSeen": "...", "lastSeen": "...", "via": "容器继承" }
]
```

---

# 第二部分：hook 与豁免规划（基于 AOSP）

## 四、已确认的 AOSP 事实

| # | 事实 | 来源 |
| --- | --- | --- |
| 1 | `android_dlopen_ext` 是 bionic 的**普通导出符号** | `platform/bionic/.../libdl/libdl.cpp` L135–137 |
| 2 | 应用 so 走 `NativeLoaderNamespace` 命名空间加载 | `platform/art/.../libnativeloader/native_loader_namespace.cpp` L55/L60/L68/L82 |
| 3 | `JavaVM::LoadNativeLibrary` 是 ART 加载 so 的核心路径 | `platform/art/.../runtime/runtime.cc` L2333/2340/2350 |

**未取到（网络受限）**：`art/libnativeloader/native_loader.cpp`（`android_dlopen_ext` 的实际调用点）

## 五、hook 的三层结构

```
第 1 层：nativeInit 时扫已加载库（现有）
  dl_iterate_phdr → patch_library_got
  覆盖：容器初始化时就已经在内存里的库

第 2 层：dlopen 时补 patch（现有，但链断了）
  hooked_dlopen → patch_newly_loaded_libraries
  问题：System.loadLibrary 的 dlopen 调用方是系统库，hook 不到

第 3 层：patch libnativeloader（新增，方向 A）
  把 /apex/com.android.art/lib64/libnativeloader.so 加入 patch 列表
  → 它的 android_dlopen_ext 调用会走 hooked_android_dlopen_ext
  → 触发 patch_newly_loaded_libraries
  → 运行期加载的库（libprobe.so / Via 的 native 部分）也能被 patch
```

## 六、豁免规划（"调用的豁免"）

### 6.1 豁免的四个维度

| 维度 | 例子 | 建议 |
| --- | --- | --- |
| **按进程** | 容器主进程整个不装 hook | ✅ 已在计划里（P0-3） |
| **按库** | `is_self` / 图形栈 | ✅ 已有 |
| **按路径** | `/data/local/tmp` 只读 | ⚠️ 已有，但要收紧 |
| **按模式** | 只读 vs 写 | ✅ p29 已修 `fopen`/`access` |

### 6.2 精确豁免清单（建议最终形态）

| 场景 | 豁免什么 | 边界 |
| --- | --- | --- |
| **容器主进程** | **完全豁免**（不装 hook） | 整个进程 |
| 导入通道 | `/data/local/tmp/` 的 **`O_RDONLY`** | 只读、只这一个目录 |
| 导入通道（可选） | `/sdcard/Download/` 的 **`O_RDONLY`** | 只读；且**依赖容器是否申请了 `MANAGE_EXTERNAL_STORAGE`** |
| `/proc/self/maps` 伪造 | 不豁免 | 所有读取都要伪造 |
| `/sdcard` 其他目录 | **不豁免** | — |
| `/system`、`/vendor`、`/apex` | **不豁免** | — |

### 6.3 三条硬规则

1. **豁免必须"路径 + 模式"双限定**（不能只按路径，否则 `fopen("r+")` 这类会漏 —— p28 的教训）
2. **每个豁免都要有对应的探针项**（`P28` 就是为此而生）
3. **不要有"全局开关"**（`NS_HOOK_SYSTEM_LIBS` 那种粗粒度开关，副作用不可预测 —— p13→p17 的教训）

## 七、实施顺序（建议）

| 阶段 | 内容 | 依赖 | 风险 |
| --- | --- | --- | --- |
| **1** | 权限分级表落地：默认不申请 `MANAGE_EXTERNAL_STORAGE`，导入改 SAF | 无 | 低 |
| **2** | P0-3：容器主进程不装 hook | 无 | 中 |
| **3** | 方向 A 步 1：打日志确认 `libnativeloader.so` 在不在加载列表 | 无 | 低 |
| **4** | 方向 A 步 2：patch `libnativeloader`，`hooked_android_dlopen_ext` 只打日志 | 3 | 中 |
| **5** | 方向 A 步 3：恢复 `patch_newly_loaded_libraries` | 4 | 中 |
| **6** | 用 `P28` 复测：预期从"能写"变成"`EACCES` 或被重定向" | 5 | — |

**阶段 1 和 2 互不依赖**，可以并行；**阶段 3–6 是一条链**。

## 八、一个必须写清楚的限制

> **即使做完上面全部，也无法在 DAC 层隔离 guest 与容器**（同 uid）。
>
> 能改善的是：
> - 容器**不主动获得**全局权限（阶段 1）
> - **容器自己**的文件访问不受 hook 影响（阶段 2）
> - **运行期加载的库**也能被路径重定向（阶段 3–5）
>
> 不能改善的是：
> - guest 通过 **native 直接系统调用**（`syscall(SYS_openat, ...)`）绕过 GOT hook
>   （容器有 `hooked_syscall`，但覆盖有限）
> - guest 通过 **`/proc/self/fd` 继承来的 fd** 访问任意位置
