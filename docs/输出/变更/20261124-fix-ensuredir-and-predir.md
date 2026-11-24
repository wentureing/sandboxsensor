# 变更计划：修 `ensureDir` 失败不重试 + 实例目录树预建

- 日期：2026-11-24
- 触发：Via 启动必崩（`IllegalArgumentException: fontDirectory == null || fontDirectory.isEmpty()`），
  用户确认 **Via 本身无故障，问题在容器**。
- 结论：容器 `SandboxManager.ensureDir()` 的"先登记、后 mkdirs、结果丢弃"逻辑，
  会把**一次失败的创建永久记忆为成功**；配合"目录树懒创建"，在 guest 启动早期产生
  "登记存在但实际不存在"的目录 → Via 的 `mkdirs()` 也失败 → 返回 null → 崩。

---

## 一、证据链

| # | 证据 | 来源 |
| --- | --- | --- |
| 1 | Via 的 `z8.c1.v(Context, name)` 反汇编为：`getExternalFilesDir` → `new File(base, name)` → `exists()` → `mkdirs()` → **失败则 `return null`** | `dist/via-unzip/classes.dex` 反汇编（`tools/tmp/dex-z8c1v-full.py`） |
| 2 | `pa.c.e(Context) → new s9.d(dir)`，`s9.d.<init>(String)` 里判断 `fontDirectory == null \|\| isEmpty()` | 同上 |
| 3 | 设备上 `<inst>/sdcard/Android/data/mark.via/` 是**空目录**（缺 `files`） | `run-as ls` |
| 4 | `SandboxManager.ensureDir`：`if (ensuredDirs.add(path)) { path.mkdirs() }`，返回值丢弃 | `SandboxManager.kt:97-102` |
| 5 | `ensuredDirs` 是 `ConcurrentHashMap.newKeySet()`，**每进程一份**；注释写明"首次使用时创建" | `SandboxManager.kt:94` |
| 6 | 旧沙箱不崩、清理重装必崩、我偶发测试不崩 | 与"懒创建 + 失败记忆"的时序竞态一致 |

## 二、影响范围

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `core/sandbox/src/main/java/com/nextvm/core/sandbox/SandboxManager.kt` | 修改（~15 行） | `ensureDir` 健壮化；新增 `ensureAppDirsReady(instanceId)` 公开方法（复用已有 `createAppDirs`） |
| `core/virtualization/.../VirtualApplicationManager.kt` | 修改（~5 行） | guest bind 时调用一次 `ensureAppDirsReady(instanceId)` |
| `probe/.../WebViewProbeActivity.java` | 修改（~30 行） | 新增 W40：复现 Via 的调用链并打印每一步结果（v1.24） |

## 三、逐文件 diff 预览

### 3.1 SandboxManager.ensureDir（核心修复）

```kotlin
// 改前
private fun ensureDir(file: File): File {
    if (ensuredDirs.add(file.absolutePath)) {
        file.mkdirs()
    }
    return file
}

// 改后
private fun ensureDir(file: File): File {
    val path = file.absolutePath
    // 已登记且确实存在 -> 快速返回（保留原有的"每进程只建一次"优化）
    if (ensuredDirs.contains(path) && file.isDirectory) return file
    // 未登记或登记过但目录已消失 -> 真正创建；只有成功才登记
    if (file.mkdirs() || file.isDirectory) {
        ensuredDirs.add(path)
    } else {
        ensuredDirs.remove(path)
    }
    return file
}
```

**关键点**：`mkdirs() || isDirectory` —— 目录已存在（并发下 mkdirs 返回 false）也算成功，
只有"既没建成、也不存在"才视为失败且**不登记**。

### 3.2 新增 `ensureAppDirsReady`

```kotlin
/**
 * v144：在 guest bind 时一次性把实例目录树建好，避免"懒创建 + 启动早期竞态"
 * 导致某层目录登记为已创建但实际不存在（曾使 Via 启动必崩）。
 */
fun ensureAppDirsReady(instanceId: String) {
    try {
        createAppDirs(instanceId)
        ensureDir(File(getExternalStorageDir(instanceId), "Android/data"))
    } catch (t: Throwable) {
        // 不阻断启动；ensureDir 已改为可重试
    }
}
```

### 3.3 VirtualApplicationManager（接线）

在 `bindApplication` 两条路径（framework / legacy）里，`registerGuestRedirections` 之后：

```kotlin
// v144：预建目录树，消除启动早期竞态
try { sandboxManager.ensureAppDirsReady(instanceId) } catch (_: Throwable) {}
```

> 需要给 `VirtualApplicationManager` 注入 `SandboxManager`（它是 `@Singleton @Inject`，
> 与 v142 给 `NativeHookBridge` 的做法一致）。**同时记得改 `AppModule` 的 `@Provides`**
> —— v142 就是漏了这处导致 CI 失败。

### 3.4 探针 W40

```java
// 复现 Via 的调用链：getExternalFilesDir -> new File(base,"font") -> exists -> mkdirs
File ext = getExternalFilesDir(null);
rec("W40 getExternalFilesDir(null) = " + ext);
rec("W40 ext.exists = " + (ext != null && ext.exists()));
File font = new File(ext, "wvprobe40_font");
rec("W40 font.exists = " + font.exists());
rec("W40 font.mkdirs = " + font.mkdirs());
rec("W40 font.exists(after) = " + font.exists());
```

判据：**`font.mkdirs = true` 且 `exists(after) = true`** 才算容器正常。

## 四、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | `ensureDir` 每次多一次 `isDirectory`（stat） | 只在"未登记"时执行；已登记且存在走快速返回。stat 成本 ~1μs |
| 2 | `createAppDirs` 在 bind 时执行，拖慢启动 | 它本来就在首次使用时执行，只是提前；且失败不阻断 |
| 3 | 注入 `SandboxManager` 到 `VirtualApplicationManager` 可能触发 Hilt 循环依赖 | 两者都是 `@Singleton`，`SandboxManager` 不依赖 `VirtualApplicationManager`，无环 |
| 4 | 忘记改 `AppModule` 的 `@Provides` → CI 失败 | 已列为 checklist（v142 踩过） |

## 五、回退方案

```powershell
git -C _ref/ShitJumpingVM revert <sha>
```
`ensureDir` 的改动本身是纯健壮性提升，风险低；若出问题优先只回退 3.3（预建调用）。

## 六、验证方法

| 步骤 | 判据 |
| --- | --- |
| 1 | 构建容器 + 探针（各 dispatch 一次） |
| 2 | `install -r` 容器（不卸载，保授权） |
| 3 | **清理 Via 实例后重装**（复现条件） |
| 4 | 跑探针 → **W40：`mkdirs=true` 且 `exists(after)=true`** |
| 5 | 启动 Via → **不再崩**（进程存活 >15s、`FATAL EXCEPTION` 计数 0、可二次进入） |
| 6 | `<inst>/sdcard/Android/data/mark.via/files/` **存在** |
| 7 | 回归：探针 W38（Java 层 `/sdcard` 重定向）仍通过；W3 仍 `ASSET_OK` |

## 七、失败判据

| 现象 | 含义 | 下一步 |
| --- | --- | --- |
| W40 `mkdirs=false` | 不是 ensureDir 的问题，是 mkdir 系统调用本身失败 | 查 errno（DAC / SELinux / 配额） |
| W40 通过但 Via 仍崩 | fontDirectory 另有来源（`getFilesDir` 分支） | 继续反汇编 `pa.c.e` 剩余单元 |
| Via 不崩但主页仍源码 | 崩溃与 MIME 是两个问题 | 回到 MIME（`记录-W2-text-plain机制收敛.md`） |
