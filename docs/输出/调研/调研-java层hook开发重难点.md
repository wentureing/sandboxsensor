# 调研：Java 层 hook 开发的重难点

- 日期：2026-11-25
- 触发：用户要求"调查 java hook 开发的重难点"
- 依据：本轮实测（`baseline-v150` 多次装机）+ 上游 v141/v147/v148 的历史 + AOSP 源码
- 目的：把"为什么这块难"讲清楚，供后续开发避免重复踩坑

---

## 零、一句话

> **Java 层 hook 的难点不在"怎么替换"，而在「替换后要完全伪装成 AOSP」——
> 异常类型、返回值、errno、重试、空参数，任何一条不一致都会让真实应用静默变坏。**

本项目已经用 Via 验证过这句话：v141 引入 hook → Via 从"可交互"退化为"工具栏点不动"（v147 定位、v148 修语义但仍默认关闭）。

---

## 一、难点 1：覆盖点选在哪一层（选错就白做）

Android 的 `java.io` 调用链是分层的，**每一层都能拦，但拦住的效果不同**：

```
应用代码
  ├─ java.io.File.mkdirs()
  │     └─ Libcore.os.mkdir()          ← android.system.Os（公开 API）
  │           └─ BlockGuardOs.mkdir()  ← 加了 StrictMode 检查
  │                 └─ Linux.mkdir()   ← native 方法（RegisterNatives 能换）
  │                       └─ libc mkdir()
  │
  ├─ java.io.FileOutputStream.write()
  │     └─ IoBridge.open()             ← java.io 自己的中间层
  │           └─ Linux.open()          ← 也可能被应用直接调
  │
  ├─ java.nio.file.Files.*             ← NIO2，走 UnixFileSystemProvider
  │     └─ UnixNativeDispatcher.*      ← **另一套 JNI**，Linux 拦不到
  │
  └─ 应用 .so 里的 open()              ← 只能靠 native GOT patch
```

**结论（本项目采用）**：拦 **`libcore.io.Linux`**（`RegisterNatives`），
因为它是 `java.io` 与 `android.system.Os` 的**共同汇聚点**。

**但要知道两个漏网**：

| 漏网 | 原因 | 影响 |
| --- | --- | --- |
| `java.nio.file.Files.*`（NIO2） | 走 `UnixNativeDispatcher`，另一个 JNI 类 | 用 NIO2 的应用会绕过 |
| 应用自带 .so 的 libc 调用 | 只有 GOT patch 能拦 | 需 native hook 配合 |

---

## 二、难点 2：语义等价（**最大难点，v147 就死在这里**）

### 2.1 异常类型必须与 AOSP 一模一样

| 方法 | AOSP 失败行为 | 错误实现 | 后果 |
| --- | --- | --- | --- |
| `Linux.open` | 抛 `libcore.io.ErrnoException` | 抛 `FileNotFoundException` | `catch (ErrnoException e) { if (e.errno == ENOENT) ... }` 的调用者**捕获不到** → 异常直冲上层 |
| `Linux.access` | 抛 `ErrnoException` | 返回 `false` | `File.canRead()` 走的是异常分支，逻辑被改变 |
| `Linux.mkdir` | 抛 `ErrnoException` | 返回 `false` | `File.mkdirs()` 的父目录探测逻辑错乱 |

**v147 实测后果**：Via 的原生工具栏/菜单**点不动**（不是崩溃，是静默失效）。

### 2.2 返回值类型不能变

| 方法 | AOSP 返回 |
| --- | --- |
| `mkdir` / `unlink` / `rename` | `void` |
| `opendir` | `long`（DIR* 的地址） |
| `stat` / `lstat` | `StructStat` **对象** |
| `open` | `FileDescriptor` **对象** |

**`stat` 系列最难**：要构造 `StructStat`，把 `struct stat` 的字段逐个映射到 Java 字段
（`st_mode` → `st_mode`、`st_size` → `st_size` …），**字段名/类型跨版本可能不同**。

**`FileDescriptor` 也有坑**：Java 侧没有公开构造器，要用隐藏方法 `setInt$(I)V`
（本项目已用：`g_fd_set_int = env->GetMethodID(g_fd_class, "setInt$", "(I)V")`）。

### 2.3 三个容易漏的行为细节

| 细节 | AOSP 行为 | 漏了会怎样 |
| --- | --- | --- |
| **EINTR 重试** | `TEMP_FAILURE_RETRY` 循环 | 信号打断时误报失败 → 偶发、难复现 |
| **`path == null`** | 抛 `NullPointerException` | 返回 `nullptr`/`0` → 上层拿到"成功但空" |
| **`FileDescriptor` 复用** | 同一个 fd 对象可多次用 | 每次 new 一个 → fd 泄漏 |

---

## 三、难点 3：注册时机与"注册了但没生效"

### 3.1 `RegisterNatives` 只能换 `native` 方法

`Linux.mkdir` 是 `native` → ✅ 可换。
但**如果某版本把它改成 Java 包装方法** → ❌ 换不了，得改 hook 策略。

### 3.2 JIT/AOT 内联

理论上 `RegisterNatives` 改的是方法表，JIT 不会缓存"方法地址"。**但**：
- 若某个调用点已被 AOT 编译成**直接调用 native 符号**（而非经 method entry），替换可能不生效
- **实测判据**：hook 后打一条 `total`，若应用明明调了 `mkdir` 但 `total` 不涨 → 就是没生效

### 3.3 类加载时机

`FindClass("libcore/io/Linux")` 必须在 `libcore` 已加载后。
本项目在 `initNativeHooks` 的 Phase 1 做 → 此时进程刚起、libcore 已就绪 ✅

---

## 四、难点 4：递归与双重翻译（本项目实测过）

### 4.1 递归

hook 内部**必须**调真实函数，不能在 hook 里再调被 hook 的名字：

```cpp
// ❌ 会无限递归（如果自己的库被 patch）
return mkdir(path, mode);

// ✅ 用真实指针
return real_mkdir(path, mode);
```

**本项目的隐患（已修 `b721cbe`）**：
`install_java_io_hooks`（Phase 1）早于 `install_plt_hooks`（Phase 2），
所以 Java hook 执行时 `real_mkdir == nullptr`，原实现 fallback 到 `::mkdir(...)`。
而 `::mkdir` 这条路**依赖"自己的库不被 GOT patch"**（`install_hooks_callback` 用
`strstr(name, "libnextvm")` 跳过）—— 库名一变就会调到 `hooked_mkdir`，
那时 `real_mkdir` 仍为 null → `ENOSYS` → **所有 mkdir 失败**。

**修法**：`install_java_io_hooks` 开头自己 `dlsym(RTLD_DEFAULT, "mkdir")`，不依赖时序。

### 4.2 双重翻译

如果 Java hook 先翻译了一次（`java_io_resolve`），内部又调 `::mkdir`，
而 `::mkdir` 被 GOT patch 到 `hooked_mkdir`（它又会 `redirect_path`）→ **翻译两次**。

**通常幂等**（沙箱路径不在规则里），但**不保证**：
若沙箱路径恰好命中某条规则（如把 `/data/user/0/com.nextvm.app/...` 也配了规则）→ 会二次篡改。

---

## 五、难点 5：诊断设施自身的时序（**本轮新踩**）

```
[+71ms] v142-redirect registered rules=13     ← 规则注册
[+78ms] guest diagnostics installed           ← DiagnosticsHolder.install
```

把"dump 统计"写在规则注册之后 → Holder 还是 `NoOp` → **输出被静默丢弃**，
排查时看起来像"hook 没生效"，实际是"观测手段没就绪"。

**教训**：任何"诊断输出为空"，第一步应打 `Holder.isInstalled`，而不是先怀疑被观测对象。

---

## 六、难点 6：性能

| 项 | 成本 | 本项目做法 |
| --- | --- | --- |
| 每次 `open` 过 JNI | ~µs 级 | 只翻译命中规则的路径 |
| `jmethodID` 每次查找 | 显著（v148 修） | 首次解析后缓存 |
| 路径字符串比较 | 每条规则一次 `startsWith` | 规则数少（13 条），可接受 |
| 日志 | 同步 I/O | **限流**（前 N 条）+ 计数，其余只累加 |

**注意**：`opendir`/`mkdir` 是**高频**调用（`listFiles` 每次都走）→ 绝不能 per-call 打日志。

---

## 七、难点 7：兼容性

| 风险 | 说明 |
| --- | --- |
| 方法签名跨版本变化 | 如 `open(String,int,int)` 在旧版是 `open(String,int)`；`mkdir` 有的版本带 `mode` 有的不带 |
| `Linux` vs `Posix` | 老版本（Android 5 之前）主要方法是 `Posix`，需要两个类都试 |
| split APK / 多 dex | 不影响（`libcore` 是系统类） |
| 加固/自带 hook 框架 | 应用可能自己也 `RegisterNatives` → **互相覆盖**，看谁后注册 |
| 32/64 位 | `long` 与 `jlong` 都是 64 位 → 无差别 |

---

## 八、本项目的实战清单（可直接复用）

### 8.1 已经做对的

| 项 | 状态 |
| --- | --- |
| 拦 `libcore.io.Linux` 而非 `java.io.File` | ✅ 汇聚点正确 |
| 失败抛 `ErrnoException(functionName, errno)` | ✅ |
| `EINTR` 重试 | ✅ |
| `FileDescriptor.setInt$` 缓存 | ✅ |
| `RegisterNatives` 个数用 `sizeof` 推导 | ✅ 加方法不用改数字 |
| `real_*` 自取（不依赖时序） | ✅ `b721cbe` |
| 统计 + 环形缓冲 + 写 `checks.log`（不依赖 logcat） | ✅ |
| 默认关闭（`DebugFlags.JAVA_IO=false`） | ✅ 保守 |

### 8.2 还没做、需要继续的

| # | 待做 | 风险 |
| --- | --- | --- |
| 1 | `stat`/`lstat`（要构造 `StructStat`） | 中（字段映射容易错） |
| 2 | `access` 的失败语义复核（当前返回 `false`？需确认是否已按 v148 改成抛异常） | **中（v147 的原罪）** |
| 3 | NIO2（`UnixNativeDispatcher`）是否要覆盖 | 低（先看有没有应用用到） |
| 4 | 按方法分批启用（先 `mkdir`，再 `opendir`） | 低（但必须做） |

### 8.3 验证方法（每加一个方法都要做）

```
第一步：只开这一个方法（其余 setprop 关）
第二步：checks.log 看 total 是否 > 0（证明 hook 真的被调到）
第三步：translated > 0（证明规则命中）
第四步：fail 的 errno 分布（ENOENT = 父目录缺失；EACCES = 权限）
第五步：真机跑 Via，看它是否变差
```

---

## 九、给下一轮的三个具体建议

1. **先补 `access` 的语义复核** —— 它是 v147 的原罪之一，而本次改动没碰它。
   若它现在仍"失败返回 false"，那么只要 `JAVA_IO=1` 就会复现 v147 的故障。

2. **`stat`/`lstat` 放在最后** —— 它们要构造 `StructStat`，是复杂度最高的一块，
   而且 `File.exists()` 依赖它；在 `mkdir`/`opendir` 验证通过前不要动。

3. **每次只开一个方法** —— 用 `setprop` 单独控制会比"全开"好定位。
   建议给 `DebugFlags` 加**按方法**的开关（而不是一个 `javaIo` 总闸）。

---

## 十、参考

- [libcore io Posix.java（AOSP diff，含 mkdir 签名）](https://android.googlesource.com/platform/libcore/+/c7fa20701d5e9398c38f4615ed293acfce1c0cf6%5E%21/luni/src/main/java/libcore/io/Posix.java)
- [libcore io IoUtils.java](https://android.googlesource.com/platform/libcore.git/+/0ac77ac8e915bff1a863e371f9b363033f9cf759/luni/src/main/java/libcore/io/IoUtils.java)
- [VirtualApp I/O Redirection System](https://deepwiki.com/asLody/VirtualApp/2.2-io-redirection-system)
- [VirtualXposed I/O Redirection](https://deepwiki.com/android-hacker/VirtualXposed/6.1-io-redirection)
- [Android Hooking cheatsheet（RegisterNatives 相关）](https://github.com/harshdhamaniya/android-security-handbook/blob/main/65-Hooking/cheatsheet.md)

---

## 十一、核实补充（写完本文后逐条查了源码）

### 11.1 `access` 的主路径其实是**对的**

`native-hook.cpp` L6859-6881 实际实现：

```cpp
int rc;
do { rc = ::access(real.c_str(), mode); } while (rc == -1 && errno == EINTR);   // ✅ EINTR 重试
if (rc == -1) {
    int err = errno;
    if (translated) java_io_record("access", real.c_str(), err, mode);
    java_io_throw_errno(env, "access", err, real.c_str());                      // ✅ 抛 ErrnoException
    return JNI_FALSE;
}
```

**所以 §2.1 表里"access 返回 false"是 v147 的旧状态，现已修**。本文更正：
- 主路径（syscall 失败）→ 已抛 `ErrnoException` ✅
- `translated` 判定用 `java_io_resolve` 的结果 ✅

### 11.2 但发现一处**真实的不一致**（新 hook 与旧 hook 不一致）

| 分支 | 新 hook（`mkdir`/`unlink`/`rename`/`opendir`） | 旧 hook（`open`/`access`） | AOSP |
| --- | --- | --- | --- |
| `path == null` | 抛 `NullPointerException` ✅ | **返回 `JNI_FALSE` / `nullptr`** ❌ | 抛 NPE |
| `java_io_resolve` 后路径为空 | `return`（void）/ `return 0` | 返回 `JNI_FALSE` / `nullptr` | 不应发生（只在我们自己解析失败时） |

**结论**：`open`/`access` 的空参数分支仍是 v141 遗留，与 AOSP 不一致。
**风险**：应用若依赖 "`File.canRead(null)` 抛 NPE" 来区分逻辑分支，会走错。
**建议**：下一轮把 `open`/`access` 的空参数分支也改用 `java_io_throw_npe`，与 4 个新 hook 对齐。

### 11.3 其余核实结果

| 项 | 核实 |
| --- | --- |
| `real_*` 自取（`b721cbe`） | ✅ 已加 `java_io_ensure_real()`，在 `install_java_io_hooks` 开头调用 |
| `RegisterNatives` 个数 | ✅ 改成 `sizeof(methods)/sizeof(methods[0])` |
| `EINTR` 重试 | ✅ `open` / `access` / 4 个新 hook 全部有 |
| `FileDescriptor.setInt$` 缓存 | ✅ 首次解析后缓存 |
| `stat`/`lstat` | ❌ 仍未覆盖（要构造 `StructStat`，复杂度最高） |
| NIO2（`UnixNativeDispatcher`） | ❌ 未覆盖 |
