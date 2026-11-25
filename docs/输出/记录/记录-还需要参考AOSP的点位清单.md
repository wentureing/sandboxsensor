# 清单：还需要参考 AOSP 的点位（设备 dex 校正版）

- 日期：2026-11-25
- 方法：三方对照
  1. `tools/tmp/UnixFileSystem_md.c`（AOSP 旧版源码，**只能作行为参考**）
  2. `tools/tmp/libcore_io_Linux.cpp`（AOSP `Linux.*` 实现）
  3. **设备 dex**（`dexdump` 读 `/apex/.../core-oj.jar`、`core-libart.jar`）—— **方法是否 native 以它为准**
  4. 我们的注册表（`native-hook.cpp` 里的 `JNINativeMethod`）

---

## 一、先纠一个陷阱：源码里的 native ≠ 设备上的 native

`UnixFileSystem_md.c`（AOSP 旧版）里有 `checkAccess` / `getLength` / `delete0` / `rename0`，
**但在设备 dex 上它们不是 native**（`getLength`/`delete`/`rename` 已是 Java 方法，
内部改调 `Os.stat` / `Os.remove` / `Os.rename`）。

**所以**：**判断"是否需要 hook"必须看设备 dex 的 `access` flag，不能照源码。**

---

## 二、设备上 `java.io.UnixFileSystem` 的 12 个 NATIVE 方法

| # | 方法 | 签名 | 我们 | 优先级 | 对应 Java API |
| --- | --- | --- | --- | --- | --- |
| 1 | `canonicalize0` | `(String,Z)String` | ❌ | **P1** | `File.getCanonicalPath()` |
| 2 | `createDirectory0` | `(File)Z` | ✅ | — | `File.mkdir/mkdirs` |
| 3 | `createFileExclusively0` | `(String)Z` | ✅ | — | `File.createNewFile` |
| 4 | `getBooleanAttributes0` | `(String)I` | ✅ | — | `exists/isFile/isDirectory/canRead/canWrite` |
| 5 | **`getLastModifiedTime0`** | `(File)J` | ❌ | **P0** | `File.lastModified()` |
| 6 | `getNameMax0` | `(String)J` | ❌ | skip | 文件名长度常量 |
| 7 | **`getSpace0`** | `(File,I)J` | ❌ | **P1** | `getUsableSpace()/getTotalSpace()` |
| 8 | `initIDs` | `()V` | ❌ | **skip（不能替换）** | native 初始化 |
| 9 | `list0` | `(File)[String` | ✅ | — | `File.listFiles()` |
| 10 | `setLastModifiedTime0` | `(File,J)Z` | ❌ | P2 | `File.setLastModified()` |
| 11 | `setPermission0` | `(File,IZZ)Z` | ❌ | P2 | `setExecutable/setReadable/setWritable` |
| 12 | `setReadOnly0` | `(File)Z` | ❌ | P2 | `File.setReadOnly()` |

**缺口：4 个已覆盖，8 个未覆盖（其中 2 个应 skip，实际待做 6 个）。**

### 2.1 为什么 `checkAccess` 不用 hook

设备 dex 显示它是 **Java 方法**，内部调 `Libcore.os.access(...)`（try/catch `ErrnoException`）。
→ **我们已 hook `Linux.access`，所以它自动被覆盖** ✅

**这也解释了 v147 为什么崩**：`checkAccess` 依赖 `ErrnoException`，
而我们的 `ErrnoException` 类名写错（`libcore.io` 而非 `android.system`）→ 抛不出来 → 崩。
**这才是"必须参考 AOSP"的真正价值** —— 不是抄代码，是理解调用方的假设。

### 2.2 为什么 `getLength` 不用 hook（但 `Linux.stat` 要）

设备 dex：`getLength` 是 Java 方法，内部 `Os.stat(path)`。
→ 需要 hook 的是 **`Linux.stat`**（见下节），不是 `getLength`。

---

## 三、设备上 `libcore.io.Linux` 的文件相关方法

AOSP 源码里有 27 个文件相关的 `Linux_*`，**我们已 hook 5 个**（`open`/`access`/`mkdir`/`unlink`/`rename`）。

### 未覆盖，按影响排序

| 优先 | 方法 | 谁在用 | 不补的后果 |
| --- | --- | --- | --- |
| **P0** | **`fstat`** | **`FileInputStream` 构造时**（本次日志已暴露：`fstat failed: EBADF`） | **读文件失败** —— 这是当前最直接的阻塞 |
| **P0** | `stat` | `UnixFileSystem.getLength`（`File.length()`）、`Os.stat` | 文件长度/属性读到宿主 |
| P1 | `lstat` | `Os.lstat`（不跟随符号链接） | 少数场景错 |
| P1 | `readlink` | `Os.readlink`、`File.getCanonicalPath` 部分路径 | 符号链接解析错 |
| P2 | `chmod` / `fchmod` | `Os.chmod`、部分 `File.set*` | 权限设置错 |
| P2 | `truncate` / `ftruncate` | `RandomAccessFile.setLength` | 截断错 |
| P2 | `realpath` | `Os.realpath` | 路径规范化错 |
| P2 | `statvfs` / `fstatvfs` | 空间查询 | 空间值错 |
| P3 | `getxattr` / `setxattr` / `listxattr` / `removexattr` | 少数 app | 扩展属性错 |
| P3 | `link` / `symlink` / `mkfifo` | 少见 | — |
| P3 | `close` / `fsync` / `fdatasync` | 无路径参数 | **不需要翻译**，可不 hook |
| **skip** | `opendir` | **`libcore.io.Linux` 里没有这个方法** | — |

---

## 四、已覆盖但需逐项核对 AOSP 行为的（**不能只看名字**）

| 方法 | 必须核对的行为点 | 现状 |
| --- | --- | --- |
| `open` | `createFileDescriptorIfOpen` 的语义；失败是否 `return NULL + 挂起异常`；`open64`/`openat` 的变体 | ✅ 已核对（null→NPE，失败→ErrnoException） |
| `openat` | **`dirfd` 相对路径的翻译**（我们目前只翻译绝对路径） | ⚠️ **未核对** |
| `access` | 失败抛 `ErrnoException` + `return (rc==0)` | ✅ 已核对 |
| `mkdir` | `throwIfMinusOne`；mode 由调用方传 | ✅ |
| `unlink` | AOSP 无 `TEMP_FAILURE_RETRY`（原样调用） | ⚠️ 我们加了 EINTR 重试，**行为略优于 AOSP**，无副作用 |
| `rename` | 两参数都检查 null；`TEMP_FAILURE_RETRY` | ✅ |
| `list0` | **扩容策略 16→翻倍**；`str_class` 空检查；错误路径 `closedir` | ⚠️ 我们直接按实际数量分配（等价）；`str_class` 未检查 null |
| `createDirectory0` | `mkdir(path, 0777)`；失败 `return false` | ✅ |
| `createFileExclusively0` | `path=="/"` 跳过；非 `EEXIST` 抛 `IOException`；`close` 失败也抛 | ✅ |
| `getBooleanAttributes0` | `stat64`；不含 `BA_HIDDEN`；失败 `return 0` | ✅ |

---

## 五、结论：还需参考 AOSP 的点位（按优先级）

### P0（阻塞"能否运行"）

| # | 点位 | 依据 |
| --- | --- | --- |
| 1 | **`Linux.fstat`** | 本次日志：`FileNotFoundException: ...json: fstat failed: EBADF` at `FileInputStream.<init>` |
| 2 | `Linux.stat` | `UnixFileSystem.getLength` → `Os.stat`（`File.length()`） |
| 3 | **`UnixFileSystem.getLastModifiedTime0`** | `File.lastModified()`；AOSP 的语义要对照 `UnixFileSystem_md.c` 的对应函数 |

### P1

| # | 点位 |
| --- | --- |
| 4 | `UnixFileSystem.canonicalize0`（失败抛 `IOException`，AOSP 已确认） |
| 5 | `UnixFileSystem.getSpace0` |
| 6 | `Linux.lstat` / `Linux.readlink` |

### P2/P3

`setPermission0` / `setReadOnly0` / `setLastModifiedTime0` / `chmod` / `truncate` / `realpath` / `statvfs` / xattr 系列

### skip

`initIDs`（不能替换）、`getNameMax0`（常量）、`opendir`（不存在）、`close`/`fsync`（无路径参数）

---

## 六、方法论：三源交叉，结论以设备 dex 为准

```
① AOSP 源码（UnixFileSystem_md.c / libcore_io_Linux.cpp）  → 看「行为」（异常/返回值/边界）
② 设备 dex（dexdump）                                      → 看「谁是真 native」
③ 我们的注册表 + 实际日志                                   → 看「谁没生效」
```

**缺任何一源都会错**：
- 只看源码 → 会去 hook `checkAccess`/`getLength` 这种已经是 Java 的方法
- 只看 dex → 不知道失败该抛什么异常
- 只看日志 → 不知道该补什么

**本轮的三次关键发现，分别来自三源**：
| 发现 | 来源 |
| --- | --- |
| `File` 的 native 在 `UnixFileSystem` 而非 `Linux` | dex |
| `createFileExclusively0` 非 EEXIST 要抛 `IOException` | 源码 |
| `ErrnoException` 类名错（`libcore.io` → `android.system`） | 日志 + dex |
