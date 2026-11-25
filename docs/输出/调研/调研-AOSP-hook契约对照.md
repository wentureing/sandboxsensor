# 调研：用本地 AOSP 源码核对 hook 契约（覆盖矩阵）

- 日期：2026-11-25
- **契约基线**（本地浅克隆，无需联网）：
  - `tools/tmp/libcore-src`（LineageOS `android_libcore`，`fd478b8`）
    - `luni/src/main/java/libcore/io/Linux.java`
    - `ojluni/src/main/java/java/io/UnixFileSystem.java`
    - `ojluni/src/main/java/sun/nio/fs/UnixNativeDispatcher.java`
  - `tools/tmp/fwb`（`aosp-mirror/platform_frameworks_base`，`1cdfff55`，blob:none）
- **被测实现**：容器 `baseline-v150 @ c7fe8fe` 的 `native-hook.cpp` `install_java_io_hooks()`（`RegisterNatives`）
- 结论先给：
  - **A 族（Linux）漏 11 个、B 族（UnixFileSystem）漏 3 个、NIO（UnixNativeDispatcher）整族 0 覆盖（45 个 native）**；
  - **但"漏"不等于"缺陷"**：按容器目标（隔离 > 可用 > 反双开）逐条归类后，
    绝大多数是**有意的设计取舍**（磁盘容量、属主、硬/软链、非路径方法）；
  - 真正需要动作的只有 **① `readlink` 泄露宿主真实路径（疑似缺陷）② `setPermission0` 可用性（待评估）③ NIO 是否构成隔离漏洞（需探针实测）**。

---

## 一、核对方法

直接从本地 git 对象读 AOSP 声明（不 checkout、不联网）：

```powershell
git -C tools/tmp/libcore-src show HEAD:ojluni/src/main/java/java/io/UnixFileSystem.java |
  Select-String 'native'
git -C tools/tmp/libcore-src show HEAD:luni/src/main/java/libcore/io/Linux.java |
  Select-String 'native'
git -C tools/tmp/libcore-src show HEAD:ojluni/src/main/java/sun/nio/fs/UnixNativeDispatcher.java |
  Select-String 'static native'
```

再与我们 `install_java_io_hooks()` 注册的 16 个方法逐一对照。

## 二、B 族：`java.io.UnixFileSystem`（`File.*` 的底层）

AOSP 声明（去掉注释掉的 `delete0`/`rename0` 与 `initIDs`）共 **11 个**：

| AOSP 方法 | 我们 | 影响的公开 API | 风险 |
| --- | --- | --- | --- |
| `canonicalize0` | ✅ | `File.getCanonicalPath` | — |
| `getBooleanAttributes0` | ✅ | `exists/isFile/isDirectory/canRead` | — |
| `getLastModifiedTime0` | ✅ | `File.lastModified` | — |
| **`setPermission0`** | ❌ | `File.setWritable/setExecutable/setReadable` | **权限探测**（容器里权限可能"看起来对"） |
| `createFileExclusively0` | ✅ | `File.createNewFile` | — |
| `list0` | ✅ | `File.list/listFiles` | — |
| `createDirectory0` | ✅ | `File.mkdir/mkdirs` | — |
| `setLastModifiedTime0` | ✅ | `File.setLastModified` | — |
| `setReadOnly0` | ✅ | `File.setReadOnly` | — |
| **`getSpace0`** | ❌ | `File.getUsableSpace/getTotalSpace` | **磁盘容量**：容器与真机不同 → 可检测 |
| **`getNameMax0`** | ❌ | `File.getNameMax`（内部） | 低（无害） |

**漏 3 个；其中 2 个（`setPermission0` / `getSpace0`）有检测价值。**

## 三、A 族：`libcore.io.Linux`（`android.system.Os` 的底层）

AOSP 中**路径相关**的 native 共 19 个：

| AOSP 方法 | 我们 | 影响 | 风险 |
| --- | --- | --- | --- |
| `open` | ✅ | `Os.open` / `FileInputStream` | — |
| `access` | ✅ | `Os.access` | — |
| `stat` / `lstat` / `fstat` | ✅ | `Os.stat/lstat/fstat` | — |
| `mkdir` | ✅ | `Os.mkdir` | — |
| `unlink` | ✅ | `Os.remove` | — |
| `rename` | ✅ | `Os.rename` | — |
| **`readlink`** | ❌ | `Os.readlink` | **符号链接探测**（沙箱路径形状） |
| **`chmod` / `fchmod`** | ❌ | `Os.chmod/fchmod` | 权限探测/修改 |
| **`chown` / `fchown` / `lchown`** | ❌ | `Os.chown/...` | 属主探测 |
| **`link` / `symlink`** | ❌ | `Os.link/symlink` | **逃逸面**（硬/软链指向宿主） |
| **`statvfs` / `fstatvfs`** | ❌ | `Os.statvfs` | **磁盘容量** → 可检测 |
| **`ftruncate`** | ❌ | `Os.ftruncate` | 低（非路径语义） |

`lseek` / `fsync` / `fdatasync` / `dup` 等非路径方法不需要路径翻译。

**漏 11 个；其中 `statvfs` 与 `readlink` 优先级最高。**

## 四、NIO：`sun.nio.fs.UnixNativeDispatcher`（`Files.*` 的底层）

AOSP 有 **48 个 `static native` 声明**（去掉 `init`/`copyToNativeBuffer` 等非 native，约 **45 个**）。
路径相关约 **30 个**：

```
open0 openat0 close fopen0 fclose
access0 exists0 stat0 stat1 lstat0 fstat fstatat0
mkdir0 rmdir0 unlink0 unlinkat0 rename0 renameat0
link0 symlink0 readlink0 realpath0 chmod0 lchown0 chown0 fchmod fchown
opendir0 fdopendir closedir readdir
statvfs0 pathconf0 fpathconf mknod0 utimes0 futimes
```

**我们：0 覆盖。**

含义：任何用 `java.nio.file.Files` / `Path` / `FileChannel` 的代码（现代 App 普遍使用）
**完全不经过我们的 Java 层 hook**；只有当它落到 libc 且所在 `.so` 被 GOT patch 时才可能被 C++ 层拦住 ——
而 `libjavacore.so` 是系统库，**不被 patch**。→ **NIO 路径当前是纯裸奔**。

> 与既有结论一致：`架构-为什么native在UnixFileSystem与跨版本兼容.md` §2.2 把 NIO 写成"计划用 RegisterNatives 覆盖"，
> 实际 0 实现。本报告把它变成**可执行的清单**。

## 五、先分清三类：**设计取舍 / 编码缺陷 / 待评估**

> 用户 2026-11-25 明确：**容器的重点是"容器"，不是和真机完全一样**；
> 有些函数实现不全可能是**正常的设计取舍**，有的才是**编码缺陷**。
> 所以本节不用"缺 N 个 = 问题"，而是逐条归类。

### 判据（只看三个后果）

| # | 后果 | 说明 |
| --- | --- | --- |
| ① | **写入/读取落到宿主真实路径**（隔离失败） | 这是容器的**底线**，出现即缺陷 |
| ② | **guest 功能异常**（应用以为不能写 / 读不到自己要的文件） | 影响可用性，视出现频率 |
| ③ | **不可接受的泄露**（把宿主真实路径/状态暴露给 guest） | 视泄露内容而定 |

- 三者都不成立，且补齐的收益小于风险/成本 → **正常取舍**（记录理由，不补）；
- 三者之一成立，且补齐成本可控 → **编码缺陷**（按优先级补）；
- 影响面不明确 → **待评估**（先加探针/实测，再决定）。

### 归类结果

| 方法族 | 归类 | 理由 |
| --- | --- | --- |
| `getSpace0` / `statvfs` / `fstatvfs` | ✅ **正常取舍** | 只影响"检测面"（磁盘容量），**不影响隔离与可用性**；且按设计理念"不含包名的路径一律不改"，反过来伪造容量本身也会引入新的不一致 |
| `getNameMax0` / `ftruncate` / `lseek` / `fsync` | ✅ 正常取舍 | 与路径无关或无害 |
| `chown` / `fchown` / `lchown` | ✅ 正常取舍 | 容器与 guest **同 uid**，改属主无意义；强行 hook 反而危险 |
| `link` / `symlink` | ✅ 正常取舍（安全优先） | 硬/软链是**逃逸面**；"宁可不做"符合"只误导、不危害读写" |
| NIO `UnixNativeDispatcher` 整族（45 个 native） | ⏸ **暂不实现（用户决定）** | 用户 2026-11-25："NIO 用得少，除了严重危害权限外，暂不实现"。当前 App 多走 `Context` 目录方法（`VirtualContext` 已给真沙箱路径），NIO 不翻译通常不影响隔离；若将来发现某 App 用 NIO 写穿宿主，再按 P0 评估 |
| **Java 层 `Linux.readlink`** | 🔶 **疑似缺陷（③ 泄露）** | `Os.readlink` 是 native 方法、实现在 `libjavacore.so`（系统库，不被 GOT patch）→ 返回**真实目标**。App 可用它把沙箱路径换算成宿主真实路径。**但**：先确认是否有 App 真的这么用（探针可加一项） |
| **B 族 `setPermission0`** | 🔶 **待评估（② 可用性）** | `File.setWritable/setExecutable` 不翻译 → 作用到不存在的宿主路径 → 静默 `false`。真机上这类调用不常见，但一旦出现，App 可能判定"不可写"而拒绝保存 |
| A 族 `chmod` / `fchmod` | 🔶 待评估 | `Os.chmod` 走 `Linux.chmod`（native，libjavacore）→ 不翻译；影响面同上，出现频率低于 `File.*` |
| A 族 `readlink`（`Os.readlink`） | 同上「Java 层 readlink」一条 |

**一句话**：真正需要动手的是 **`readlink`（泄露）** 与 **`setPermission0`（可用性）** 两个候选，
外加 **NIO 是否构成隔离漏洞**需要一次探针实测；其余缺口是**有意的设计取舍**，不应作为"缺陷"去补。

## 六、优先级（只对"缺陷/待评估"）

| 优先 | 项 | 为什么 | 建议动作 |
| --- | --- | --- | --- |
| **P0** | 探针补一项：**NIO 写入是否落宿主**（`Files.write` 到宿主形态路径） | 决定 NIO 是"边界"还是"缺陷" | 探针加 `F15`（`Files.write` + 检查宿主目录） |
| **P1** | Java 层 `Linux.readlink` | 泄露宿主真实路径（③） | 若探针确认有实际使用 → 补 hook；否则记录为边界 |
| **P1** | `setPermission0` | 可用性（②） | 先加探针确认频率，再决定 |
| P2 | A 族 `chmod/fchmod` | 同上、频率更低 | 随 P1 一起评估 |
| — | `getSpace0/statvfs`、`chown`、`link/symlink`、`getNameMax0` | **设计取舍** | **不做**，仅在文档登记理由 |

## 七、制度化：把"核对"变成脚本（可反复跑）

AOSP 源码在 `tools/tmp/`（不入库、随时可重克隆），所以做成**离线对照脚本**：

```
tools/lint/check-hook-coverage.mjs
  输入：--aosp <libcore-src 路径>（缺省 tools/tmp/libcore-src）
        --impl <native-hook.cpp 路径>
  输出：三族方法的 已 hook / 未 hook 表 + **按三类（取舍/缺陷/待评估）标注**
  退出码：默认 0（只提示）；仅当 `--strict` 且存在「编码缺陷」类缺口时非 0
```

**为什么需要它**：本次缺口是靠"人工把三份 AOSP 文件读一遍"发现的；
下次 Android 版本变化、或我们新增 hook 时，需要一条命令就能重新对照。

## 八、本次核对顺带确认的事实

| # | 事实 | 依据 |
| --- | --- | --- |
| 1 | `libcore.io.Linux` 是 `public final class Linux implements Os`，方法是**实例 native**（`public native`） | 源码第 50 行起 |
| 2 | `UnixFileSystem` 的 native 全是 `private native`，且 `delete0`/`rename0` 在**当前分支已被注释** | 源码 |
| 3 | NIO 的 native 全是 `private static native`，入口是 `UnixNativeDispatcher` | 源码 |
| 4 | 本地 `fwb` 只有 `core/java/android/os/Environment.java` 在 index 里（blob:none 克隆），其余需 `git show` 读取 | `git ls-files` 只有 1 条 |
