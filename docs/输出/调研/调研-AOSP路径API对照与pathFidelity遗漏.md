# 对照：AOSP 里所有返回路径的 API vs 我们已覆盖的（遗漏清单）

- 日期：2026-11-25
- 方法：从 `aosp-mirror/platform_frameworks_base` 提取 `ContextImpl` / `ApplicationInfo` /
  `Environment` / `StorageVolume` 里所有「返回 File / String 且名字含 Dir/Path/File」的
  方法与字段，与 `VirtualConstants.synthetic*` 逐一对照
- 脚本：`tools/tmp/search-path-apis.py`
- 源码：`tools/tmp/fwb`（partial clone，`--filter=blob:none` + `http.sslBackend=openssl`）

---

## 一、`android.app.ContextImpl`（20 个入口）

| # | 方法 | 我们 | 判定 |
| --- | --- | --- | --- |
| 1 | `getFilesDir` | ✅ `syntheticDataDir/files` | 覆盖 |
| 2 | `getCacheDir` | ✅ `syntheticDataDir/cache` | 覆盖 |
| 3 | `getCodeCacheDir` | ✅ `syntheticDataDir/code_cache` | 覆盖 |
| 4 | `getNoBackupFilesDir` | ✅ `syntheticDataDir/no_backup` | 覆盖 |
| 5 | `getExternalFilesDir` | ✅ `syntheticExternalDir/files[/type]` | 覆盖（本轮新增） |
| 6 | `getExternalFilesDirs` | ✅ 转发给 5 | 覆盖 |
| 7 | `getObbDir` | ✅ `syntheticObbDir` | 覆盖（本轮新增） |
| 8 | `getObbDirs` | ✅ 转发给 7 | 覆盖 |
| 9 | `getExternalCacheDir` | ✅ `syntheticExternalCacheDir` | 覆盖（本轮新增） |
| 10 | `getExternalCacheDirs` | ✅ 转发给 9 | 覆盖 |
| 11 | `getDatabasePath` | ✅ `syntheticDataDir/databases/<name>` | 覆盖 |
| 12 | `getDataDir` | ✅ `syntheticDataDir` | 覆盖 |
| 13 | `getDir` | ✅ `syntheticDataDir/app_<name>` | 覆盖 |
| **14** | **`getFileStreamPath(name)`** | ❌ | **遗漏（P0）** |
| **15** | **`getPackageCodePath()`** | ❌ | **遗漏（P0）** |
| **16** | **`getPackageResourcePath()`** | ❌ | **遗漏（P0）** |
| **17** | `getExternalMediaDirs` | ❌ | **遗漏（P1，已记录）** |
| 18 | `getPreloadsFileCache` | ❌ | **不需要** —— 返回 `/data/preloads/...`，系统路径，不含 app 包名 |
| 19 | `getSharedPreferencesPath(name)` | ❌ | **不需要** —— `VirtualContext.getSharedPreferences` 已把文件扁平化到宿主目录（`nextvm_<instanceId>_<name>.xml`），不走这条 |
| 20 | `getCrateDir` | — | 脚本把 `getCacheDir` 重复匹配了一次（正则跨行），不是新 API |

---

## 二、`android.content.pm.ApplicationInfo`（12 个路径字段）

| # | 字段 | 我们 | 判定 |
| --- | --- | --- | --- |
| 1 | `sourceDir` | ✅ 经 `syntheticApkPath` 规则 | 覆盖 |
| 2 | `publicSourceDir` | ✅ 同上 | 覆盖 |
| 3 | `nativeLibraryDir` | ✅ 经 `syntheticLibDir` 规则 | 覆盖 |
| **4** | **`dataDir`** | ❌ | **遗漏（P1，但 v146 有教训）** |
| **5** | **`deviceProtectedDataDir`** | ❌ | **遗漏（P1）** |
| **6** | **`credentialProtectedDataDir`** | ❌ | **遗漏（P1）** |
| 7 | `scanSourceDir` / `scanPublicSourceDir` | ❌ | 少见（包扫描器用），P3 |
| 8 | `secondaryNativeLibraryDir` | ❌ | 少见（多 ABI），P3 |
| 9 | `nativeLibraryRootDir` | ❌ | 少见，P3 |
| 10 | `getAllApkPaths` / `getCodePath` / `getBaseResourcePath` | ❌ | 少见，P3 |

**关于 `dataDir` 的 v146 教训**：
`VirtualContext` 的 `getDataDir()` 注释写着「v146 已证明改 `getApplicationInfo().dataDir` 无效」——
意思是**改它修不好 MIME 问题**，不等于**不该改**。但既然 v147 已经改用
`Context.getDataDir()` 解决，`ApplicationInfo.dataDir` 是否要合成需要单独评估
（它同样会暴露 `/data/user/0/com.nextvm.app/files/virtual/data/<instance>`）。

---

## 三、`android.os.Environment`（12 个）—— **全部不需要**

| 方法 | 为什么不需要 |
| --- | --- |
| `getExternalStorageDirectory()` | 返回共享存储根 `/storage/emulated/0`，**不含包名**，真机就是这个值 |
| `getExternalStoragePublicDirectory(type)` | 同上，`/storage/emulated/0/<type>` |
| `getExternalDirs()` | 同上，卷根数组 |
| `getDataDirectory()` / `getDataDirectoryPath()` | `/data` —— 系统根，不含包名 |
| `getDataSystemDirectory()` / `getUserSystemDirectory()` / `getUserConfigDirectory()` | `/data/system...` —— 系统路径 |
| `getDataSystemCe/DeDirectory()` / `getDataMisc*` / `getDataVendor*` | 系统路径 |
| `getExpandDirectory()` | `/data/expand` |

**实现依据**：`Environment.java` 的这些方法全部来自进程级静态 `sCurrentUser`
（`UserEnvironment`），返回的是**卷视图**。改它等于改整个进程的共享存储视图，
副作用远大于收益，而且它**本来就不暴露沙箱**。

---

## 四、`android.os.storage.StorageVolume`（4 个）—— **全部不需要**

`getPath()` / `getInternalPath()` / `getPathFile()` / `getDirectory()`
返回**卷路径**（`/storage/emulated/0`），不含包名。

---

## 五、遗漏清单（按优先级）

### 更正：原 P0 三条是**误报**，它们已自动跟随

| # | API | 实际结论 |
| --- | --- | --- |
| 1 | `Context.getFileStreamPath(name)` | **不需要单独处理** —— `Context` 的默认实现是 `makeFilename(getFilesDir(), name)`，而 `getFilesDir()` 已在 pathFidelity 分支返回合成路径，**自动跟随** |
| 2 | `Context.getPackageCodePath()` | **不需要** —— 返回 `ApplicationInfo.sourceDir`，而 `PackageManagerProxy` 已在 pathFidelity 时设成 `syntheticApkPath` |
| 3 | `Context.getPackageResourcePath()` | 同上，走 `sourceDir` / `publicSourceDir` |

**实测依据**（`PackageManagerProxy.kt` L752-758）：

```kotlin
if (VirtualConstants.isPathFidelityGuest(app.packageName)) {
    dataDir = VirtualConstants.syntheticDataDir(app.packageName)
    nativeLibraryDir = VirtualConstants.syntheticLibDir(app.packageName)
    val synthApk = VirtualConstants.syntheticApkPath(app.packageName)
    sourceDir = synthApk
    publicSourceDir = synthApk
}
```

**教训**：这已经是本轮第三次「凭印象判定遗漏 → 实际已覆盖」。
判断某路径有没有被合成，必须**搜代码里有没有设置它的地方**，
不能只看 `VirtualContext` 有没有 override —— `ApplicationInfo` 的字段是在
`PackageManagerProxy` 里设的。

### P1 —— 会暴露，但不那么常用

> **2026-11-25 已补齐 4~7 四项**（见下文第七节的完成记录）。

| # | API | 说明 |
| --- | --- | --- |
| 4 | ~~`Context.getExternalMediaDirs()`~~ | **已补**：`syntheticMediaDir` + `VirtualContext` 分支 + 4 条 media 规则 |
| 5 | ~~`ApplicationInfo.dataDir`~~ | **已覆盖**（`PackageManagerProxy` L753） |
| 6 | ~~`ApplicationInfo.deviceProtectedDataDir`~~ | **已补**（= `dataDir`） |
| 7 | ~~`ApplicationInfo.credentialProtectedDataDir`~~ | **已补**（= `dataDir`） |

### P3 —— 少见

`scanSourceDir` / `scanPublicSourceDir` / `secondaryNativeLibraryDir` /
`nativeLibraryRootDir` / `getAllApkPaths`

### 不需要

`getPreloadsFileCache` · `getSharedPreferencesPath` · `Environment.*` · `StorageVolume.*`

---

## 六、与"只误导、不危害读写"原则的对应

用户提出的原则：**pathFidelity 只是为了误导，不能危害实际读写。**

对应到实现，有三条可检验的约束：

| # | 约束 | 检验方式 |
| --- | --- | --- |
| 1 | **每个合成路径都必须被某条规则覆盖** | `T39 每个合成路径都有对应规则` —— 穷举所有 `synthetic*` 路径，逐个验证 `translatePath` 会改写它，且不是 `/dev/null` |
| 2 | **合成路径翻译必须幂等** | `T40 合成路径翻译幂等` —— 翻译结果再翻译不变，且落在沙箱内 |
| 3 | **合成路径与沙箱真身一一对应** | `T41 合成路径与沙箱真身一一对应` —— 不允许两个合成路径映射到同一处（反例：`external_data/` 与 `sdcard/Android/data/<pkg>/` 曾同时被当成 external 真身） |

**这三条测试是原则的可执行表达**。任何新加 `synthetic*` 函数，如果忘了配套规则，
T39 会红；如果规则目标撞车，T41 会红。

### 还需要补的第 4 条约束（**未实现，建议加**）

> **合成路径的父目录必须在沙箱内真实存在**，否则 `File.mkdirs()` 会在假路径上
> 尝试创建（被 hook 映射后能成功，但若映射漏了就会失败在真路径上）。

检验方式：对每个 `synthetic*`，先 `translatePath`，再验证目标目录存在（运行时测试，
需要设备；纯 JVM 测不了）。可以放到探针里做。

---

## 七、下一步（**暂不执行**）

| 优先 | 动作 |
| --- | --- |
| P0 | 补 `getFileStreamPath` 的 pathFidelity 分支（`syntheticDataDir/files/<name>`） |
| P0 | 确认 `getPackageCodePath` / `getPackageResourcePath` 当前返回什么（可能已经通过 `ApplicationInfo.sourceDir` 的规则被覆盖，需实测） |
| P1 | 评估 `ApplicationInfo.dataDir` / `deviceProtectedDataDir` / `credentialProtectedDataDir` |
| P1 | 补 `getExternalMediaDirs` |

---

## 八、2026-11-25 补齐记录

| 改动 | 文件 | 内容 |
| --- | --- | --- |
| 1 | `core/model/.../ProcessSlot.kt` | 新增 `syntheticMediaDir(pkg)` = `/storage/emulated/0/Android/media/<pkg>` |
| 2 | `core/sandbox/.../VirtualContext.kt` | `getExternalMediaDirs()` 加 `pathFidelity` 分支 |
| 3 | `core/binder/.../PackageManagerProxy.kt` | `deviceProtectedDataDir` / `credentialProtectedDataDir` 设为 `dataDir`（合成值） |
| 4 | `core/hook/.../NativeHookBridge.kt` | `setupAppRedirections` 新增 4 条 media 规则（`/storage/emulated/0/Android/media/` 与 `/sdcard/Android/media/`，各含/不含尾斜杠） |

**第 4 条是关键**：只合成路径而不加规则，就是用户说的"危害实际读写"。
T39（每个合成路径都有对应规则）会守住这一点。
