# 对照 AOSP 源码指出的实现错误（完整清单，暂不修改）

- 日期：2026-11-25
- 依据（真实源码，非推导）：
  - `tools/ref/libcore_io_Linux.cpp`（2916 行，`LineageOS/android_libcore` @ `lineage-21.0`）
  - `tools/ref/UnixFileSystem_md.c`（572 行，同仓）
- 状态：**只列出，不修改**（用户指定：CI 期间先对照）

> **自我更正（2026-11-25）**：本清单初稿曾把 `Linux.mkdir`/`unlink`/`rename` 的空参分支
> 列为「未抛 NPE」。复核源码后确认**它们已经调用了 `java_io_throw_npe`**（`c71d7ed` 时就加了），
> 属于我的误判，已从清单中删除。教训：**对照也要对照自己的代码，不能凭记忆。**

---

## 一、AOSP 真实语义（原文摘录）

### 1.1 `libcore_io_Linux.cpp`

```cpp
static jobject Linux_open(JNIEnv* env, jobject, jstring javaPath, jint flags, jint mode) {
    ScopedUtfChars path(env, javaPath);
    if (path.c_str() == NULL) return NULL;
    int fd = throwIfMinusOne(env, "open", TEMP_FAILURE_RETRY(open(path.c_str(), flags, mode)));
    return createFileDescriptorIfOpen(env, fd);
}

static jboolean Linux_access(JNIEnv* env, jobject, jstring javaPath, jint mode) {
    ScopedUtfChars path(env, javaPath);
    if (path.c_str() == NULL) return JNI_FALSE;
    int rc = TEMP_FAILURE_RETRY(access(path.c_str(), mode));
    if (rc == -1) throwErrnoException(env, "access");
    return (rc == 0);
}

static void Linux_mkdir(JNIEnv* env, jobject, jstring javaPath, jint mode) {
    ScopedUtfChars path(env, javaPath);
    if (path.c_str() == NULL) return;
    throwIfMinusOne(env, "mkdir", TEMP_FAILURE_RETRY(mkdir(path.c_str(), mode)));
}

static void Linux_unlink(JNIEnv* env, jobject, jstring javaPathname) {
    ScopedUtfChars pathname(env, javaPathname);
    if (pathname.c_str() == NULL) return;
    throwIfMinusOne(env, "unlink", unlink(pathname.c_str()));
}

static void Linux_rename(JNIEnv* env, jobject, jstring javaOldPath, jstring javaNewPath) {
    ScopedUtfChars oldPath(env, javaOldPath);
    if (oldPath.c_str() == NULL) return;
    ScopedUtfChars newPath(env, javaNewPath);
    if (newPath.c_str() == NULL) return;
    throwIfMinusOne(env, "rename", TEMP_FAILURE_RETRY(rename(oldPath.c_str(), newPath.c_str())));
}
```

**统一模式**：
- **null 参数** → `ScopedUtfChars` 内部**抛 NPE**，随后 `return`（异常已挂起）
- **syscall 失败** → `throwIfMinusOne` / `throwErrnoException` → **抛 `ErrnoException`**
- **EINTR** → `TEMP_FAILURE_RETRY`

### 1.2 `UnixFileSystem_md.c`

```c
// list0
WITH_FIELD_PLATFORM_STRING(env, file, ids.path, path) { dir = opendir(path); }
END_PLATFORM_STRING(env, path);
if (dir == NULL) return NULL;                       // 失败返回 NULL，不抛
...
while ((ptr = readdir64(dir)) != NULL) {
    if (!strcmp(ptr->d_name, ".") || !strcmp(ptr->d_name, "..")) continue;   // 跳过 . 和 ..
    ...
}

// createDirectory0
WITH_FIELD_PLATFORM_STRING(env, file, ids.path, path) {
    if (mkdir(path, 0777) == 0) rv = JNI_TRUE;      // mode = 0777
}

// createFileExclusively0  ← 与我们的实现差异最大
WITH_PLATFORM_STRING(env, pathname, path) {
    if (strcmp(path, "/")) {                        // ← ① 根目录直接跳过
        fd = handleOpen(path, O_RDWR | O_CREAT | O_EXCL, 0666);
        if (fd < 0) {
            if (errno != EEXIST)
                JNU_ThrowIOExceptionWithLastError(env, path);   // ← ② 非 EEXIST 抛 IOException
        } else {
            if (close(fd) == -1)
                JNU_ThrowIOExceptionWithLastError(env, path);   // ← ③ close 失败也抛
            rv = JNI_TRUE;
        }
    }
}

// getBooleanAttributes0
if (statMode(path, &mode)) {                        // statMode 内部用 stat64（跟随符号链接）
    int fmt = mode & S_IFMT;
    rv = BA_EXISTS | (fmt==S_IFREG ? BA_REGULAR : 0) | (fmt==S_IFDIR ? BA_DIRECTORY : 0);
}                                                    // ← 不含 BA_HIDDEN
return rv;                                           // 失败返回 0

// canonicalize0
if (canonicalize(path, canonicalPath, PATH_MAX) < 0) {
    JNU_ThrowIOExceptionWithLastError(env, "Bad pathname");   // ← 抛 IOException，不是返回 null
} else {
    rv = JNU_NewStringPlatform(env, canonicalPath);
}
```

---

## 二、逐项对照结果

### P0 未修（会改变可见行为）

| # | 方法 | AOSP 行为 | 我们的实现 | 后果 |
| --- | --- | --- | --- | --- |
| **1** | **`createFileExclusively0`** | ① `path == "/"` 直接跳过 ② `open` 失败且 `errno != EEXIST` **抛 `IOException`** ③ `close` 失败也抛 | ①未处理 ②**返回 `false`** ③忽略 `close` 返回值 | **`File.createNewFile()` 在权限/目录缺失时"静默失败"**（应抛 `IOException`），app 以为"文件已创建"却不存在 |

### P1 未实现

| # | 方法 | AOSP | 我们的 | 影响 |
| --- | --- | --- | --- | --- |
| 5 | `UnixFileSystem.canonicalize0` | 失败**抛 `IOException`** | **完全未实现** | `File.getCanonicalPath()` 走原实现 → 返回**宿主路径** → 后续用它拼路径会逃逸 |
| 6 | `Linux.stat` / `lstat` | 抛 `ErrnoException`，返回 `StructStat` | **未在 Java 层 hook**（只有 GOT patch） | `File.length()` 走 `Os.stat` → 读到**宿主**的大小 |

### P2 近似（不严格一致，但影响小）

| # | 项 | AOSP | 我们的 |
| --- | --- | --- | --- |
| 7 | `open` 的 UTF 转换失败分支 | 返回 `NULL`，**异常已挂起（OOM）** | 抛 `EINVAL`（近似：都让调用方看到异常） |
| 8 | `list0` 的 `str_class` 获取 | `CHECK_NULL_RETURN(str_class, NULL)` | `FindClass` 后**未检查 null**（`NewObjectArray` 会因 null 失败，但会多挂一个异常） |

### 已修正（本轮 `ad0d391` 之前的提交）

| # | 项 | 状态 |
| --- | --- | --- |
| 9 | `getBooleanAttributes0` 多返回 `BA_HIDDEN(0x08)` | ✅ 已删 |
| 10 | `createDirectory0` 用 `0770`（应 `0777`） | ✅ 已改 |
| 11 | `open`/`access` 空参返回 `null`/`false` 而不抛 | ✅ 已改（`d0d9e3d`） |

### 已确认一致（无需改）

| 项 | 结论 |
| --- | --- |
| `list0` 跳过 `.` 和 `..` | ✅ 一致 |
| `list0` `opendir` 失败 `return NULL`（不抛） | ✅ 一致 |
| `getBooleanAttributes0` 用 `stat`（`stat64`，跟随符号链接） | ✅ 一致 |
| `createDirectory0` 失败 `return false`（不抛） | ✅ 一致 |
| `createFileExclusively0` 的 `O_RDWR\|O_CREAT\|O_EXCL` + `0666` | ✅ 一致 |
| `access` 失败抛 `ErrnoException`（v147 的修正是对的） | ✅ 一致 |
| `Linux_rename` 用 `TEMP_FAILURE_RETRY` | ✅ 等价（`do/while`） |
| **`mkdir`/`unlink`/`rename` 空参抛 NPE** | ✅ 一致（实测源码：`if (path == nullptr) { java_io_throw_npe(...); return; }`） |

---

## 三、最值得关注的一条：`createFileExclusively0`

**这是本轮对照中新发现的、影响最大的偏差**：

```c
// AOSP
if (fd < 0) {
    if (errno != EEXIST)
        JNU_ThrowIOExceptionWithLastError(env, path);   // ← 抛！
}
```

```cpp
// 我们的
if (fd == -1) {
    g_java_io_fs_fail.fetch_add(1);
    java_io_record(...);
    return JNI_FALSE;                                   // ← 只是返回 false
}
```

**为什么影响大**：
- `File.createNewFile()` 是 `boolean` 返回值，但 **AOSP 在"非 EEXIST 失败"时抛 `IOException`**
- 应用通常写 `if (file.createNewFile()) {...} else {...}` —— 但**也会**写
  `try { file.createNewFile(); } catch (IOException e) {...}`
- **我们返回 `false` 而不抛** → 后一种应用**认为"创建成功"**（无异常）→ 后续写入失败

**这正是"仿真不足"的典型**：返回值对了（`false`），但**异常语义错了**。

---

## 四、建议的修复顺序（下次执行，不在本轮）

| 优先 | 动作 |
| --- | --- |
| **P0** | `createFileExclusively0`：补 `path == "/"` 跳过 + 非 `EEXIST` 抛 `IOException` + `close` 失败抛 |
| **P0** | `mkdir`/`unlink`/`rename` 空参：调用 `java_io_throw_npe` |
| P1 | 新增 `Linux.stat`/`lstat` 的 Java 层 hook（`File.length()` 依赖） |
| P1 | 新增 `UnixFileSystem.canonicalize0`（失败抛 `IOException`） |
| P2 | `list0` 的 `str_class` null 检查 |
