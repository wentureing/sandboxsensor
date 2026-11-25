# 架构：为什么那些 native 方法在那里，以及怎么做到跨 app / 跨系统

- 日期：2026-11-25
- 触发：用户指出「关键不是对比签名，而是知道**为什么** java 函数在那个文件，
  换一个 app、换一个系统还怎么兼容」
- 结论先行：
  1. **"在哪个类"不是随意的** —— 由 JDK 1998 年就定下的 `FileSystem` 抽象决定
  2. **换 app 天然兼容** —— 我们 hook 的是**API 层**，不是某个 app 的代码
  3. **换系统要"绑定契约"而不是"绑定类名"** —— 反射 `File.fs` 拿实现类

---

## 一、为什么 native 在 `UnixFileSystem`（而不是 `libcore.io.Linux`）

### 1.1 设备上验证出的结构

```
java.io.File            super=Object           字段 fs : Ljava/io/FileSystem;
java.io.FileSystem      super=Object           flags = ABSTRACT
java.io.UnixFileSystem  super=java.io.FileSystem
```

`FileSystem` 的**抽象契约**（跨平台）：

```
ABSTRACT createDirectory(File)Z
ABSTRACT createFileExclusively(String)Z
ABSTRACT delete(File)Z
ABSTRACT list(File)[String
ABSTRACT listRoots()[File
ABSTRACT rename(File,File)Z
```

`UnixFileSystem` 把它们实现为 **native**（`*0` 后缀是 native 约定）：

```
NATIVE list0(File)[String              -> File.list/listFiles
NATIVE createDirectory0(File)Z          -> File.mkdir/mkdirs
NATIVE createFileExclusively0(String)Z  -> File.createNewFile
NATIVE getBooleanAttributes0(String)I   -> File.exists/isFile/isDirectory/canRead
NATIVE canonicalize0(String,Z)String    -> File.getCanonicalPath
NATIVE getSpace0(File,I)J               -> File.getUsableSpace/getTotalSpace
NATIVE setPermission0(File,IZZ)Z        -> File.setExecutable/setReadable/setWritable
NATIVE setLastModifiedTime0(File,J)Z    -> File.setLastModified
NATIVE getLastModifiedTime0(File)J      -> File.lastModified
NATIVE setReadOnly0(File)Z              -> File.setReadOnly
NATIVE getNameMax0(String)J             -> 文件名长度上限
NATIVE initIDs()V                       -> native 初始化（缓存 jfieldID）
```

### 1.2 这个分层是 JDK 的设计，不是 Android 的随意选择

```
java.io.File                     ← 公开 API（跨平台，JDK 1.0 起）
   │  静态字段 fs : FileSystem    ← 委托点
   │  方法内部调 fs.list(file) 等
   ▼
java.io.FileSystem               ← 抽象类（平台无关契约）
   │  abstract list / createDirectory / delete / rename ...
   ▼
java.io.UnixFileSystem           ← Unix/Linux 实现
   │  list0 / createDirectory0 ... 声明为 native
   ▼
C++（libcore_io_UnixFileSystem.cpp）
```

**为什么要抽象**：JDK 要同时支持 Unix 与 Windows（`WinNTFileSystem`）。
`File` 只写一次，平台差异全在 `FileSystem` 实现里。

> **所以"在哪个类"这个问题的答案是：由 `File` → `FileSystem` → 实现 这条委托链决定。
> 每一个 `File.xxx()` 都对应实现类里的一个 `xxx0` native 方法。**

### 1.3 那 `libcore.io.Linux` 是干嘛的？

**两条路并存，都是公开 API**：

| 用途 | 路径 | 设计意图 |
| --- | --- | --- |
| **`java.io.File`**（标准 Java） | `File` → `FileSystem` → `UnixFileSystem` → native | 兼容 JDK 抽象 |
| **`android.system.Os`**（Android 特有） | `Os` → `BlockGuardOs` → `Linux` → native | 把 syscall 直接暴露给应用 |

所以：

- `File.listFiles()` → **`UnixFileSystem.list0`**（不走 Linux）
- `Os.mkdir()` / `File.canRead()` 里某些路径 → **`Linux.mkdir` / `Linux.access`**

**两者都要 hook，缺一不可** —— 这正是之前只 hook `Linux` 覆盖不到 `File` 的原因。

---

## 二、换一个 app 怎么兼容

### 2.1 关键：我们 hook 的是"API 层"，不是"某个 app"

```
任意 app 的代码
   │  只用 java.io.File / android.system.Os / java.nio.file.Files
   ▼
【我们 hook 的位置】  UnixFileSystem.*0  /  Linux.*  /  UnixNativeDispatcher.*
   ▼
libc
```

**app 不可能绕过**（除非它自己写 .so 直接调 libc —— 那由 native GOT patch 兜底）。

**所以"换 app"不是问题** —— 只要它走 Java/NIO 标准 API，就必然经过我们 hook 的那几个点。

### 2.2 覆盖矩阵（这是"兼容性"的真实含义）

| 层 | 入口类 | 谁在用 | 覆盖方式 |
| --- | --- | --- | --- |
| **L1** | `java.io.UnixFileSystem.*0` | **`File` 的一切**（99% 的应用） | `RegisterNatives` |
| **L2** | `libcore.io.Linux.*` | `android.system.Os`、部分 `File` 内部路径 | `RegisterNatives` |
| **L3** | `sun.nio.fs.UnixNativeDispatcher.*` | NIO2（`Files.*`、`Path`） | ❌ **计划，0 实现**（2026-11-25 核实：全仓无该类的 `RegisterNatives`） |
| **L4** | libc（`open`/`mkdir`/...） | 应用自己的 .so、WebView 等 | native GOT patch |

**要"换 app 也兼容"，就是把 L1–L3 都覆盖；L4 已经用 GOT patch 覆盖了。**

**这就是为什么必须去查 `UnixFileSystem`** —— 不是因为它"在某个文件里"，
而是因为**它是 L1 层的唯一实现入口**。

---

## 三、换一个系统怎么兼容（**核心设计原则**）

### 3.1 版本变化只有三种形态

| 变化形态 | 例子 | 影响 |
| --- | --- | --- |
| **① 打包边界变了，类名没变** | Android 10 起 `java.*` 从 `core-libart.jar` 移到 `core-oj.jar` | **完全不影响**（`Class.forName` 按类名找，不按 jar） |
| **② 实现类改名** | `UnixFileSystem` → 某版本改成别的名字 | hook 失效 |
| **③ 方法名/签名变** | `list0` → `list`；`mkdir` 参数从 `int` 变 `long` | hook 失效 |

**所以不能让代码硬编码 ② 和 ③ 的名字。**

### 3.2 正确做法：绑定**契约**，不绑定**实现**

```cpp
// ❌ 现在（脆弱）：硬编码实现类名
jclass ufs = FindClass("java/io/UnixFileSystem");

// ✅ 应该（健壮）：从 File.fs 反射拿当前系统的实际实现类
// 1) 拿 java.io.File 的静态字段 fs
jclass fileCls = FindClass("java/io/File");
jfieldID fsField = GetStaticFieldID(fileCls, "fs", "Ljava/io/FileSystem;");
jobject fs = GetStaticObjectField(fileCls, fsField);

// 2) 取它的实际实现类（UnixFileSystem / 未来叫什么都行）
jclass implCls = GetObjectClass(fs);

// 3) 在实现类上反射找 native 方法：优先 xxx0，回退 xxx
//    只要 FileSystem 的抽象契约还在（JDK 契约，极稳定），就能找到
RegisterNatives(implCls, ...);
```

**为什么这样能跨版本**：

| 变化 | 用 `File.fs` 反射的效果 |
| --- | --- |
| 实现类改名 | ✅ 自动拿到新实现类 |
| jar 位置变 | ✅ 不影响（只按类名/字段找） |
| 方法名从 `list0` 变 `list` | ✅ 可以先探 `list0`，不存在再探 `list` |
| 抽象契约本身变了（如新增方法） | ⚠️ 需要新增 hook（但**旧方法仍能覆盖**） |

**唯一真正会破的是**：JDK 不再用 `FileSystem` 抽象（概率极低，1998 年至今没变）。

### 3.3 `android.system.Os` 那条路也一样

```
android.system.Os
   └── 静态字段 Libcore.os : Os  →  实际实现 BlockGuardOs
                                        └─ 继承 ForwardingOs → 委托 Linux
```

同样可以**反射 `Libcore.os` 拿实现类**，而不是硬编码 `BlockGuardOs`/`Linux`。

### 3.4 落地清单（如果要改成自适应）

| # | 改动 | 收益 |
| --- | --- | --- |
| 1 | 用反射 `File.fs` 拿实现类，替代硬编码 `java/io/UnixFileSystem` | 跨版本、跨厂商 |
| 2 | 方法名探测：`list0` 优先，失败回退 `list` | 兼容命名变化 |
| 3 | 用 `Libcore.os` 拿 `Os` 实现类，替代硬编码 `Linux` | 同上 |
| 4 | 把"实际拿到的类名 + 注册成功的方法"写进 `checks.log` | 换版本时一眼看出适配到哪 |
| 5 | 审计工具（`tools/device/jni-sig-audit.py`）保留为**离线的对照手段** | 出问题时能快速定位 |

**注意**：3.4 的 1–3 是**设计建议，尚未实施**。当前代码仍是硬编码（已能跑，因为钉在当前系统上）。

---

## 四、回答用户三个问题的摘要

### Q1：为什么 java 函数在那个文件？

> **因为 JDK 的 `FileSystem` 抽象**：`File`（公开 API）→ `FileSystem`（抽象类）→
> `UnixFileSystem`（平台实现，`*0` 方法为 native）。
> `File` 的每一个操作都对应实现类里的一个 `xxx0` native 方法。
> 这是 1998 年就定下的设计，Android 沿用。
> 另一条 `android.system.Os` → `BlockGuardOs` → `Linux` 是 Android 额外的 syscall 暴露层。

### Q2：换一个 app 怎么兼容？

> **不需要为 app 做任何事**。我们 hook 的是 API 层（`UnixFileSystem.*0` / `Linux.*` /
> `UnixNativeDispatcher.*`）。任何走 `java.io.File` / `Os` / `Files` 的应用都必然经过这些点。
> 唯一例外是自带 .so 直接调 libc 的应用 —— 那由 native GOT patch 覆盖。

### Q3：换一个系统怎么兼容？

> **绑定契约，不绑定类名**：
> - 反射 `File.fs` 拿到**当前系统的实际实现类**（不硬编码 `UnixFileSystem`）
> - 方法名探测（`list0` → 回退 `list`）
> - jar 位置变化**完全不影响**（`Class.forName` 按类名找）
>
> 只要 `FileSystem` 这个抽象还在（JDK 契约，极稳定），hook 就能自动适配。
> **审计工具只是离线对照手段，不是兼容机制本身。**

---

## 五、一句话

> **"在哪个类"由 `File` → `FileSystem` → 实现 的委托链决定，是 JDK 的稳定契约；
> "能不能跨系统"取决于你是绑类名还是绑契约 —— 绑 `File.fs` 就能自适应。**
