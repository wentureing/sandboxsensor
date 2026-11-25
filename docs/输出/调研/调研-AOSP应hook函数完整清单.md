# 调研：从 LineageOS/android_libcore 全仓搜出的「应 hook 函数」完整清单

- 日期：2026-11-25
- 方法：**不靠记忆**。遍历 `LineageOS/android_libcore`（8066 个文件）的 git 树，
  对每个 `native/*.c`、`native/*.cpp`、`*_md.c` 提取所有 `Java_*` 与 `Linux_*` 入口，
  按文件系统关键字筛选，再与 `native-hook.cpp` 的 `JNINativeMethod` 表逐条对照
- 脚本：`tools/tmp/search-aosp-hook-list.py`
- 源码：`tools/tmp/libcore-src`（`git clone --depth 1 https://github.com/LineageOS/android_libcore.git`）

---

## 一、统计总览

| 组 | AOSP 里与文件相关的入口 | 我们已 hook | 未 hook |
| --- | --- | --- | --- |
| `java.io.UnixFileSystem`（`UnixFileSystem_md.c`） | 16 | **8** | 8 |
| `libcore.io.Linux`（`libcore_io_Linux.cpp`） | 38 | 5（Java 层）+ 3（仅 native GOT） | 30 |
| `sun.nio.fs.UnixNativeDispatcher`（NIO2） | 37 | **0** | 37 |

**注意一个易混淆点**：脚本初版把 `stat`/`lstat`/`fstat` 判成"已 hook"，
因为它们出现在 `native-hook.cpp` 里 —— 但那只是 **GOT patch 的 `dlsym` 变量**，
**Java 层没有 `RegisterNatives` 注册**。所以 Linux 组的真实 Java 层覆盖是 **5 个**。

---

## 二、`java.io.UnixFileSystem`（16 个入口）

### 已 hook（8）

| 方法 | 对应 Java API |
| --- | --- |
| `list0(File)[String` | `File.listFiles` |
| `createDirectory0(File)Z` | `File.mkdir/mkdirs` |
| `createFileExclusively0(String)Z` | `File.createNewFile` |
| `getBooleanAttributes0(String)I` | `exists/isFile/isDirectory/canRead/canWrite` |
| `getLastModifiedTime0(File)J` | `lastModified` |
| `setLastModifiedTime0(File,J)Z` | `setLastModified` |
| `setReadOnly0(File)Z` | `setReadOnly` |
| `canonicalize0(String,Z)String` | `getCanonicalPath` |

### 未 hook（8）

| 方法 | 优先级 | 说明 |
| --- | --- | --- |
| `getSpace0(File,I)J` | **P1** | `getUsableSpace/getTotalSpace` —— 不 hook 会拿宿主磁盘值，可能误判空间不足 |
| `setPermission0(File,IZZ)Z` | P2 | `setExecutable/setReadable/setWritable` |
| `getNameMax0(String)J` | skip | 文件名长度常量 |
| `initIDs()V` | **skip（不能替换）** | native 初始化，缓存 jfieldID |
| `checkAccess` | skip | **设备 dex 上是 Java 方法**，内部调 `Libcore.os.access`，已被 `Linux.access` 覆盖 |
| `delete0` | skip | **设备 dex 上不是 native**；`File.delete` 走 `Os.remove` → `Linux.unlink` |
| `getLength` | skip | **设备 dex 上是 Java 方法**；走 `Os.stat` → 需 hook `Linux.stat` |
| `rename0` | skip | **设备 dex 上不是 native**；走 `Os.rename` → `Linux.rename` |

> **重要**：`UnixFileSystem_md.c` 是旧版源码。设备 dex 上 `checkAccess`/`delete0`/`getLength`/`rename0`
> 已经不是 native（Android 改成 Java 实现 + 走 `Os.*`）。**判断是否需要 hook 必须以设备 dex 的
> `access` flag 为准**（详见 `docs/输出/记录/记录-还需要参考AOSP的点位清单.md`）。

---

## 三、`libcore.io.Linux`（38 个入口）

### 已 hook（Java 层 5 个）

`open` · `access` · `mkdir` · `unlink` · `rename`

### 仅在 native GOT 层覆盖（Java 层未 hook，3 个）

`stat` · `lstat` · `fstat`

**这三条是当前最该补的**：`File.length()` 走 `Os.stat`，`FileInputStream` 构造走 `fstat`
（本轮的 `fstat failed: EBADF` 就是它）。

### 未 hook（30 个）—— 按影响分级

| 优先 | 方法 | 谁在用 |
| --- | --- | --- |
| **P0** | `stat` / `lstat` / `fstat` | `Os.stat` / `FileInputStream` / `File.length` |
| P1 | `readlink` | `Os.readlink`、`getCanonicalPath` 部分路径 |
| P2 | `chmod` / `fchmod` | `Os.chmod`、部分 `File.set*` |
| P2 | `truncate` / `ftruncate` | `RandomAccessFile.setLength` |
| P2 | `realpath` | `Os.realpath` |
| P2 | `statvfs` / `fstatvfs` | 空间查询 |
| P3 | `getxattr` / `setxattr` / `listxattr` / `removexattr` | 少数 app 读文件属性 |
| P3 | `link` / `symlink` / `lchown` | 少见 |
| P3 | `mkfifo` / `mknod` | 极少 |
| **skip** | `close` / `readBytes` / `writeBytes` / `preadBytes` / `pwriteBytes` / `readv` / `writev` / `lseek` / `msync` / `fsync` / `fdatasync` | **无路径参数**，不需要翻译 |
| **skip** | `listen` / `memfd_create` | 与文件路径无关（socket / 匿名文件） |

---

## 四、`sun.nio.fs.UnixNativeDispatcher`（NIO2，37 个入口）

**目前 0 个 Java 层 hook。** 实测 app 利用率极低（5 个样本里 `Files.*` 只有 0–4 次 invoke），
但**一旦用了就全走这条路**。

| 优先 | 方法 | 说明 |
| --- | --- | --- |
| P1 | `opendir0` / `fdopendir` / `readdir` / `closedir` | `Files.newDirectoryStream` / `Files.list` |
| P1 | `open0` / `openat0` | `Files.newInputStream` / `newOutputStream` |
| P1 | `stat0` / `lstat0` / `fstatat0` | `Files.readAttributes` |
| P2 | `mkdir0` / `unlink0` / `rename0`(NIO2) / `rmdir0` | `Files.createDirectory` / `delete` / `move` |
| P2 | `chmod0` / `chown0` / `fchmod` / `fchown` / `lchown0` | `Files.setPosixFilePermissions` |
| P2 | `link0` / `symlink0` / `readlink0` | `Files.createLink` / `createSymbolicLink` |
| P3 | `mknod0` / `futimes` / `fopen0` / `fclose` / `getcwd` | 少见 |
| **skip** | `close` / `read`(NIO2)/ `write`(NIO2) | 无路径参数 |

---

## 五、结论：优先级排序（**暂不执行**）

| 优先 | 动作 | 理由 |
| --- | --- | --- |
| **P0** | 补 `Linux.stat` / `lstat` / `fstat` 的 Java 层 hook | `fstat failed: EBADF` 已实测阻塞 `FileInputStream`；`File.length()` 读宿主 |
| P1 | 补 `UnixFileSystem.getSpace0` | 空间误判可能导致应用拒绝保存 |
| P1 | 补 `Linux.readlink` / `realpath` | 路径规范化 |
| P2 | 补 `UnixFileSystem.setPermission0` / `Linux.chmod` | 权限判断 |
| P3 | NIO2 整组（37 个） | 实测几乎不用，但用了就全落空 |
| skip | 全部无路径参数的方法（`close`/`read*`/`write*`/`lseek`/`fsync`…） | 不需要路径翻译 |

**P0 的实现难点**：`stat`/`lstat`/`fstat` 要返回 `android.system.StructStat`，
而它的构造器是 14 参（`JJIJIIJJJJJJJ`）。AOSP 用旧式 15 参（带 3 个 `StructTimespec`），
设备 dex 上两种都在。**实现前必须先确认设备上哪个构造器可用**，否则会重演
`ErrnoException` 类名写错那类错误。
