# 让 SO 落在「与 guest 包名一致的路径」：实现细节

- 日期：2026-11-22
- 目标：guest 看到的 SO 路径形如 `/data/user/0/<guest_pkg>/lib/arm64/libx.so`
- 动机：`/proc/self/maps` 检测（见 `记录-native-hook作用域与污染.md` §6.1）

---

## 0. 先讲清一个硬约束（决定了整个方案形态）

**物理上不可能**把文件真放进 `/data/user/0/<guest_pkg>/`：

| 障碍 | 说明 |
| --- | --- |
| **DAC** | `/data/user/0/<other_pkg>` 属主是该应用 UID，权限 `0700`；容器是 `untrusted_app`（自己 UID），无写权限 |
| **SELinux** | `untrusted_app` 域不能写 `app_data_file` 的其他应用目录 |
| **`/data/user/0` 本身** | `0711 root:root`，容器无法在其中创建新目录 |
| **无 root** | 不能 `mount --bind`、不能 `unshare(CLONE_NEWNS)`（Android 10+ 禁非特权 user ns） |

→ **所以 B1 的"路径一致"只能做到「视图一致」，不是「物理一致」。**

这决定了实现必须分三层，缺一不可：

```
第 1 层  Java 字段伪造   —— 让 ApplicationInfo 里的路径字符串看起来对
第 2 层  路径翻译       —— 让虚路径的 IO 真正落到沙箱文件
第 3 层  maps 伪造      —— 让 /proc/self/maps 显示虚路径（内核只会给真实路径）
```

---

## 1. 第 1 层：Java 字段伪造

`LoadedApk` / `ClassLoader` / `ApplicationContext` 拿到的路径全部来自 `ApplicationInfo`（和 `LoadedApk` 构造参数）。

### 需要改的字段

| 字段 | 现状（容器真实值） | 目标（虚值） |
| --- | --- | --- |
| `packageName` | `com.nextvm.app` | `<guest_pkg>` |
| `sourceDir` | `files/virtual/apks/<inst>.apk` | `/data/app/<guest_pkg>-1/base.apk` |
| `publicSourceDir` | 同上 | 同上 |
| `splitSourceDirs` | 沙箱 split 路径 | `/data/app/<guest_pkg>-1/split_config.xx.apk` |
| `dataDir` | `files/virtual/data/<inst>` | `/data/user/0/<guest_pkg>` |
| `deviceProtectedDataDir` | 同上 | `/data/user_de/0/<guest_pkg>` |
| `credentialProtectedDataDir` | 同上 | `/data/user/0/<guest_pkg>` |
| **`nativeLibraryDir`** | `files/virtual/data/<inst>/lib` | **`/data/user/0/<guest_pkg>/lib/arm64`** |
| `secondaryNativeLibraryDirs` | — | `/data/user/0/<guest_pkg>/lib`（32 位） |
| `uid` | 宿主 uid | 保持宿主 uid（**不要伪造**，伪造会破坏权限） |
| `processName` | `com.nextvm.app:p0` | 保持（伪造需额外处理 `/proc/self/cmdline`） |

### 关键：`ClassLoader` 的 librarySearchPath

`LoadedApk` 会用 `nativeLibraryDir` 构建 `librarySearchPath`：

```
<dataDir>/lib:/data/app/<pkg>-1/base.apk!/lib/arm64
```

`System.loadLibrary("x")` → `Runtime.nativeLoad(...)` → `dlopen("/data/user/0/<guest_pkg>/lib/arm64/libx.so")`

**注意**：`dlopen` 的参数是**虚路径**，此时物理文件不存在 → 必须靠第 2 层翻译。

---

## 2. 第 2 层：路径翻译（虚 → 实）

### 翻译表

| 虚路径前缀 | 实路径前缀 |
| --- | --- |
| `/data/user/0/<guest_pkg>/` | `/data/user/0/com.nextvm.app/files/virtual/data/<inst>/` |
| `/data/data/<guest_pkg>/` | 同上（`/data/data` 是 `/data/user/0` 的软链） |
| `/data/user_de/0/<guest_pkg>/` | `files/virtual/data_de/<inst>/` |
| `/data/app/<guest_pkg>-1/` | `files/virtual/apks/<inst>/`（APK 用单文件 + `/lib` 子目录） |
| `/sdcard/`、`/storage/emulated/0/` | `files/virtual/sdcard/<inst>/`（已有） |

### 必须 hook 的符号（按优先级）

```
P0  open / openat / open64 / __openat_2 / creat
    stat / lstat / stat64 / lstat64 / fstatat / statx
    access / faccessat
    fopen / fopen64 / freopen
P0  opendir / fdopendir / readdir / readdir64
    mkdir / rmdir / unlink / unlinkat / rename / renameat
    mmap / mmap64（文件映射，需先把 fd 换成真实 fd）
P1  readlink / readlinkat
    statfs / statvfs
    link / symlink / readlink
    truncate
P1  execve（如果 guest 要执行沙箱内的可执行文件）
```

### 实现要点

```c
static const char* translate(const char* vpath) {
    / 返回线程局部缓冲里的实路径；未命中返回 vpath 原样
    if (starts_with(vpath, g_virt_data_prefix)) {
        snprintf(tls_buf, sizeof(tls_buf), "%s%s",
                 g_real_data_prefix,
                 vpath + strlen(g_virt_data_prefix));
        return tls_buf;
    }
    / ... 其他前缀
    return vpath;
}
```

**必须注意的坑**：

| 坑 | 说明 |
| --- | --- |
| 相对路径 | `openat(AT_FDCWD, "lib/x.so")` + 前置 `chdir` 之后，相对路径要基于当前目录解析（或直接 `openat` 到真实目录 fd） |
| `O_PATH` / `O_NOFOLLOW` | 不要破坏语义 |
| 符号链接 | 沙箱内若有符号链接，`realpath` 可能跑出沙箱 → 需二次校验 |
| `readlink` | 真实路径要**翻译回虚路径**（反方向） |
| 线程安全 | 翻译缓冲必须 thread-local，否则多线程串 |
| 长度 | `PATH_MAX` 是 4096，拼接要检查截断 |
| 已有 fd | 已经 open 的 fd 无法"重新翻译"，所以翻译必须发生在 open 时刻 |

---

## 3. 第 3 层：`/proc/self/maps` 伪造（最麻烦的一层）

### 为什么必须伪造

`maps` 的内容由**内核**从 `vm_file->f_path.dentry` 生成。`dlopen` 无论用虚路径还是实路径打开，只要落到同一个 inode，`maps` 就显示**物理路径**：

```
7f12345000-7f1234a000 r-xp 00000000 fd:05 12345 /data/user/0/com.nextvm.app/files/virtual/data/<inst>/lib/arm64/libx.so
```

→ 必须拦截读取，返回伪造内容。

### 实现路径

```c
/ 只在 scope 打开时生效
int hooked_open(const char* path, int flags, ...) {
    if (is_proc_maps(path)) {           / /proc/self/maps、/proc/<pid>/maps
        return make_faked_maps_fd();
    }
    ...
}
```

`make_faked_maps_fd()`：

1. 用 `memfd_create("maps", MFD_CLOEXEC)` 建一个匿名文件
2. 读真实 `/proc/self/maps`
3. 逐行做前缀替换：
   ```
   /data/user/0/com.nextvm.app/files/virtual/data/<inst>/   →  /data/user/0/<guest_pkg>/
   /data/user/0/com.nextvm.app/files/virtual/apks/<inst>/    →  /data/app/<guest_pkg>-1/
   /data/app/com.nextvm.app-xxx/lib/arm64/libnextvm-native.so → 整行删除
   /data/user/0/com.nextvm.app/...                            → 整行删除（宿主私有文件）
   ```
4. `write()` 到 memfd，`lseek(0)`
5. 返回该 fd

### 必须同时处理的变体

| 路径 | 处理 |
| --- | --- |
| `/proc/self/maps` | 伪造 |
| `/proc/<tid>/maps` | 伪造（同内容） |
| `/proc/self/smaps` | 伪造（smaps 比 maps 多字段，替换规则相同） |
| `/proc/self/smaps_rollup` | 伪造 |
| `/proc/self/numa_maps` | 伪造 |
| `/proc/self/task/*/maps` | 伪造 |
| `/proc/self/map_files/*` | 这些是符号链接，`readlink` 时翻译 |

### 三个必须知道的坑

1. **`stat()` 的大小**：伪造 fd 的 `st_size` 是 memfd 实际大小，与真实 maps 不同。某些检测会比较 `stat` 与 `read` 长度是否一致 → 需要 `fstat` hook 返回合理值。
2. **`mmap` 读取**：如果检测方用 `mmap` 读 maps（少见但存在），memfd 可 mmap ✓（这是选 memfd 而非 pipe 的原因）。
3. **多线程 + 频繁读取**：每次 `open` 都生成一份 memfd → 必须配对 `close`，否则 fd 泄漏；建议加 LRU 缓存（同一次 maps 内容在 100ms 内复用）。

---

## 4. 分阶段落地（MVP → 完整）

| 阶段 | 内容 | 效果 | 工作量 |
| --- | --- | --- | --- |
| **A. 只做第 1+2 层** | 伪造 `ApplicationInfo` + 路径翻译 | 功能正常；字面量 `/data/data/<pkg>` 可用；`maps` 仍暴露 | 中（1–2 天） |
| **B. 加 maps 伪造（仅 `maps`）** | 第 3 层最小实现 | 公开检测方案中的 maps 比对失效 | 中（2–3 天） |
| **C. 补齐变体** | smaps/numa_maps/map_files/task | 抗更细的检测 | 小 |
| **D. 抗 `ps`/`cmdline`** | 进程数伪装、`/proc/self/cmdline` 伪造 | 抗"同 UID 进程数"检测 | 中（需处理 `/proc` 目录枚举） |

**建议顺序：A（先让功能对）→ B（再让检测面收敛）**。

---

## 5. 与当前容器的差距（现状盘点）

| 项 | 现状 | 差距 |
| --- | --- | --- |
| `ApplicationInfo.dataDir` | 用**真实**沙箱路径 `files/virtual/data/<inst>` | 需要改成虚路径 + 翻译 |
| `ApplicationInfo.sourceDir` | 真实 APK 路径 | 需改虚路径 |
| `nativeLibraryDir` | 真实 lib 目录 | 需改虚路径 |
| 路径翻译 | 已有（`/sdcard` 等） | 需扩展虚拟前缀 |
| `maps` 伪造 | **完全没有** | 需新建模块 |
| `readlink` | 部分 | 需要反向翻译 |

---

## 6. 一个更省事的替代思路（值得评估）

**如果目标 guest 不做 maps 检测**，那么第 1+2 层（阶段 A）就够了 —— 功能完全正常，且不必碰 maps 伪造的高风险区。

**判断依据**：用探针在容器内跑 `N03`（native 字面量路径）+ 一个新探针项（读 `/proc/self/maps` 并输出前 20 行），看目标 guest 是否会主动读 maps。

**结论**：
> 优先做阶段 A（虚路径 + 翻译），它解决"字面量路径 SDK 兼容"这个确定性问题；
> 阶段 B（maps 伪造）只在明确有检测对抗需求时再做，因为它的复杂度与风险都在前者的 2–3 倍。

---

## 7. 重要反例：从 memfd 加载 SO 在 Android 上失败

来源：用户提供的 `dist/questions77921799.txt`（StackOverflow #77921799，2024-02）

### 7.1 那个问题的做法与结果

```c
int fd = syscall(SYS_memfd_create, "lib", 0);
write(fd, elf_buf, elf.size);
sprintf(load_path, "/proc/self/fd/%d", fd);
System.load(load_path);           / 失败
```

```
java.lang.UnsatisfiedLinkError: dlopen failed: library "/proc/self/fd/76" not found
```

`dlopen("/proc/self/fd/76")` 直接返回 NULL，`dlerror` 同一条消息。

### 7.2 为什么失败（Android 与桌面 Linux 的关键差异）

| 原因 | 说明 |
| --- | --- |
| **linker namespace 白名单** | Android 的 `classloader-namespace` 只允许加载特定目录（`/data/app/*/lib`、`/vendor/lib`、`/system/lib` 等）。`/proc/self/fd/*` 不在允许列表，`open_library` 直接拒绝 |
| **fd 生命周期** | 若 `memfd` 未带 `MFD_CLOEXEC` 处理不当，或 Java 层 `System.load` 触发 GC/其他线程关闭 fd，路径会立刻失效 |
| **`/proc/self/fd/N` 是符号链接** | linker 在 namespace 校验阶段会 `realpath`，解析结果是 `/memfd:lib (deleted)`，既不在白名单也不含 `.so` 后缀 |
| **官方立场** | Android 明确不支持"从内存加载 native 库"，这是加固/防注入策略的一部分 |

> 桌面 Linux 上 `x-c3ll.github.io/posts/fileless-memfd_create` 的 fileless 技巧成立，但 **Android 的 linker 做了额外限制**，不能照搬。

### 7.3 对我们的 B1 方案意味着什么

**好消息：我们不受这个限制影响。**

该问题失败的根本原因是"**文件不在磁盘上**"。而 B1 的方案里，SO **始终是磁盘上的真实文件**，只是路径字符串被映射：

```
guest 看到的：  /data/user/0/<guest_pkg>/lib/arm64/libx.so
物理存在的：    /data/user/0/com.nextvm.app/files/virtual/data/<inst>/lib/arm64/libx.so
```

`dlopen` 拿到的虚路径 → 我们的 `open/openat` hook 翻译成实路径 → linker 正常 `mmap` 真实文件。

**但引出三个必须验证的点**：

| # | 风险 | 验证方法 |
| --- | --- | --- |
| 1 | linker 是否在 `open` 之前就对路径做 namespace 校验？如果它先 `realpath` 虚路径，会得到 ENOENT | 用探针 `System.load("/data/user/0/<fake>/lib/arm64/libprobe.so")` 实测 |
| 2 | linker 会不会绕过我们的 `open` hook（直接用 `openat` + 特殊 flag）？ | 在 hook 里加日志，看 `dlopen` 时是否有对应调用 |
| 3 | `librarySearchPath` 里的虚路径，是否会被 linker 校验为"不在 namespace" | 同上测试 |

### 7.4 推荐的探针验证（在动手做阶段 A 之前）

新增两项探针，直接在容器里跑：

```java
/ J37: 用"虚拟路径"加载 SO，验证 open hook 是否被 linker 走到
try {
    System.load("/data/user/0/com.example.fake/lib/arm64/libprobe.so");
    report("J37.load_virtual_path", PASS, "loaded");
} catch (Throwable t) {
    report("J37.load_virtual_path", FAIL, t.toString());
}

/ J38: 读 /proc/self/maps，看 SO 的显示路径
/ （同时验证第 3 层 maps 伪造的必要性）
```

判据：
- `J37` PASS → 阶段 A 的路径翻译对 linker 有效，B1 可做
- `J37` FAIL 且错误是 `library not found` → linker 在校验阶段就拒绝了虚路径，需要另找办法（例如 `android_dlopen_ext` + `ANDROID_DLEXT_USE_LIBRARY_FD` 传真实 fd，但那样 maps 又会暴露真实路径）

### 7.5 一个可能绕过 linker 校验的备选（记录备查）

`android_dlopen_ext` 支持 `ANDROID_DLEXT_USE_LIBRARY_FD`：

```c
android_dlextinfo info = { .flags = ANDROID_DLEXT_USE_LIBRARY_FD, .library_fd = real_fd };
void* h = android_dlopen_ext("libx.so", RTLD_NOW, &info);
```

- **优点**：直接传真实 fd，linker 不校验路径
- **缺点**：`maps` 里显示的是 fd 对应的真实 inode 路径 → 仍然暴露，需要第 3 层伪造
- **用途**：如果阶段 A 被 linker 拦住，这是可行退路

