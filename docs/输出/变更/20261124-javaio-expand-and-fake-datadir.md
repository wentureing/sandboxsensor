# 变更计划：Java 层重定向补全 + 白名单假 dataDir（任务 1+2）

- 日期：2026-11-24
- 用户指令：「尝试 1 和 2」（1 = 扩展 Java 层重定向方法；2 = 假 dataDir 让 WebView 走标准分支）
- 前置：v141（`RegisterNatives` 换 `libcore.io.Linux.open/access`）+ v142（guest 补注册 11 条规则）已打通，
  W38 验收通过（写 `/sdcard` 落沙箱）

---

## 一、目标

| 任务 | 目标 |
| --- | --- |
| **1** | 把 Java 层重定向从 `open/access` 扩到 `unlink/rename/mkdir/readlink` + `UnixFileSystem` 的
`getBooleanAttributes0/createFileExclusively0/createDirectory0`，让**存在性判断、删除、建目录**也走翻译 |
| **2** | 对**白名单 guest（先只 `com.sandbox.probe`）** 把 `ApplicationInfo.dataDir` 改成 guest 官方路径
（`/data/user/0/<guest>`），使 WebView 的 `file://` 不落在"自己的 dataDir 内"，从特殊分支落到标准
`FileURLLoader` 分支，从而按扩展名判 MIME |

## 二、影响范围

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `core/hook/src/main/cpp/native-hook.cpp` | 修改 | 新增 ~150 行：7 个 hooked 方法 + 路径取用 helper；扩充 `JNINativeMethod` 表 |
| `core/sandbox/.../VirtualContext.kt` | 修改 | `getApplicationInfo()`：**白名单 guest 返回副本**（其余保持原逻辑，避免扩大影响面） |
| `core/virtualization/.../VirtualApplicationManager.kt` | 修改 | 新增 `patchFrameworkDataDir()`，仅白名单 guest；在 framework bind 路径调用 |
| `probe/.../WebViewProbeActivity.java` | 修改 | 新增 W39（Java 层 exists/delete/mkdir/createNewFile 是否走沙箱）；v1.23 |

**三个开关/白名单**（全部默认保守）：
- `nextvm.javaIoRedirect`（已有，默认 1）
- `nextvm.webviewDataDirFake`（默认 0，本次**默认关**，测的时候手动开）
- 白名单：`com.sandbox.probe`

## 三、逐文件 diff 预览

### 3.1 native-hook.cpp（新增，插在 `install_java_io_hooks` 前）

```cpp
// 通用：std::string 版本的前缀翻译（供 File 参数的方法复用）
static std::string java_io_translate_str(const char* raw) {
    if (raw == nullptr) return "";
    if (!g_java_io_translate.load()) return std::string(raw);
    std::string redirected = redirect_path(raw);
    return redirected.empty() ? std::string(raw) : redirected;
}

// File 对象 → getPath()
static void java_io_file_path(JNIEnv* env, jobject fileObj, std::string* out)

// 7 个 hooked 方法
hooked_java_linux_unlink(JNIEnv*, jclass, jstring)
hooked_java_linux_rename(JNIEnv*, jclass, jstring, jstring)
hooked_java_linux_mkdir (JNIEnv*, jclass, jstring, jint)
hooked_java_linux_readlink(JNIEnv*, jclass, jstring)          → jstring
hooked_java_ufs_attrs(JNIEnv*, jobject, jobject /*File*/)      → jint (BA_EXISTS|REGULAR|DIRECTORY|HIDDEN)
hooked_java_ufs_create_file(JNIEnv*, jobject, jstring)         → jboolean (O_CREAT|O_EXCL)
hooked_java_ufs_create_dir(JNIEnv*, jobject, jobject /*File*/) → jboolean
```

注册表扩为 9 项（`open`/`access` + 上面 7 个）。

### 3.2 VirtualContext.kt

```kotlin
override fun getApplicationInfo(): ApplicationInfo {
    // v143：白名单 guest 返回副本，避免原地修改共享的 LoadedApk.mApplicationInfo
    // （不改白名单的路径，影响面严格限制）
    val base = super.getApplicationInfo()
    val info = if (needsDetachedAppInfo()) ApplicationInfo(base) else base
    ... 原逻辑改为对 info 操作 ...
}
private fun needsDetachedAppInfo(): Boolean =
    System.getProperty("nextvm.webviewDataDirFake", "0") != "0" &&
        guestPackageName == "com.sandbox.probe"
```

### 3.3 VirtualApplicationManager.kt（新增方法）

```kotlin
private fun patchFrameworkDataDir(app: Application, guestPkg: String) {
    if (System.getProperty("nextvm.webviewDataDirFake", "0") == "0") return
    if (guestPkg != "com.sandbox.probe") return
    val fake = VirtualConstants.syntheticDataDir(guestPkg)   // /data/user/0/<pkg>
    val loadedApk = fieldOf(app.javaClass, "mLoadedApk").get(app) ?: return
    fieldOf(loadedApk.javaClass, "mApplicationInfo").get(loadedApk)
        ?.let { (it as ApplicationInfo).dataDir = fake }
    fieldOf(loadedApk.javaClass, "mDataDir").set(loadedApk, fake)
    GuestBreadcrumb.log("v143-datadir pkg=$guestPkg dataDir=$fake")
}
```

调用点：framework bind 分支，`registerGuestRedirections` 之后、`registerBreadcrumbCallbacks` 之前。

### 3.4 探针 W39（v1.23）

```java
// Java 层：exists / createNewFile / mkdir / delete 是否都走沙箱
File sd = new File("/sdcard/wvprobe39.txt");
rec("W39 createNewFile = " + sd.createNewFile());
rec("W39 exists = " + sd.exists());
File dir = new File("/sdcard/wvprobe39dir");
rec("W39 mkdir = " + dir.mkdir());
rec("W39 delete = " + sd.delete());
rec("W39 exists after delete = " + sd.exists());
```

判据：`createNewFile/exists/mkdir/delete` 全部成功且行为自洽 = 三个 native 方法都通了。

## 四、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | `getBooleanAttributes0` 实现错误 → 所有 `File.exists()` 返回错误 → 应用行为大面积异常 | 只用 `::stat` 语义实现；`BA_*` 常量按 AOSP 定义（EXISTS=1/REGULAR=2/DIRECTORY=4/HIDDEN=8） |
| 2 | `createFileExclusively0` 用 `O_CREAT\|O_EXCL`，权限位与 AOSP 不同（0666 vs 0600） | 用 0666（AOSP 用 0666 并受 umask） |
| 3 | 假 dataDir 让 framework 内部 Java 读文件失败 | 现在 Java 层重定向已通（v141+v142），`/data/user/0/<guest>/` → 沙箱 是**已注册**的规则（11 条里包含） |
| 4 | `VirtualContext` 返回副本影响该 guest 全部 `getApplicationInfo()` | 严格白名单 + 开关默认关 |
| 5 | `list0`（目录列表）本次不做 → `File.listFiles()` 仍不翻译 | 明确列为后续；W39 不测 listFiles |

## 五、回退方案

```powershell
git -C _ref/ShitJumpingVM revert <本次提交sha>      # 容器
git revert <探针提交sha>                            # 探针
```
或直接把 `nextvm.webviewDataDirFake` / `nextvm.javaIoRedirect` 设为 `0`（运行时关掉，无需重新构建）。

## 六、验证方法

| 步骤 | 命令/判据 |
| --- | --- |
| 1 | 构建容器 + 探针（各 dispatch 一次） |
| 2 | `install -r` 容器（**不卸载**，避免丢授权） |
| 3 | 跑探针（等 15s），读 `wvprobe-result.txt` |
| 4 | **W39 判据**：`createNewFile/exists/mkdir/delete` 全部成功且 `exists after delete = false` |
| 5 | **W37 判据**（开 `nextvm.webviewDataDirFake` 时）：`dataDir = /data/user/0/com.sandbox.probe` |
| 6 | **W2 判据**（核心目标）：`ct` 从 `text/plain` 变成 `text/html` |
| 7 | 回归：W3 仍 `ASSET_OK`；Via 启动不崩 |

## 七、判据失败时的下一步

| 现象 | 含义 | 下一步 |
| --- | --- | --- |
| W39 失败 | native 扩展有 bug | 只看失败的单个方法，逐个排查 |
| W37 仍沙箱 | 副本/白名单没生效 | 检查 `getProperty` 是否在 fork 前设置 |
| W37 变假但 W2 仍 `text/plain` | **假说被否**：dataDir 归属不是 MIME 的变量 | 停止这条路线，回到代读（需解决注入时机） |
