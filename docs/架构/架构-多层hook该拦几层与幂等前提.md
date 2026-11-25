# 调研：多层系统里，一次调用该 hook 一层还是多层

- 日期：2026-11-25
- 触发：用户问「调研这种多层次的系统，一次调用 hook 一层就可以还是多层」
- 结论先行：
  1. **判据不是"层"，而是"这个入口有没有 native 方法"**
  2. `File` 本身**没有 native 方法**（纯 Java 包装）→ **hook 不到** → **必须 hook 它分叉的所有 native 点**
  3. 如果某一层**自己就是 native**（如 `list0`）→ **hook 那一层就够**，下层靠**翻译幂等**兜底
  4. 幂等性已实测验证（沙箱路径不命中任何规则）→ 多层并存不会双重翻译

---

## 一、先看清一条调用链到底长什么样

以 `File.listFiles()` 为例：

```
app code
  │  File.listFiles()                      ← 纯 Java，无 native 方法
  ▼
java.io.FileSystem.list(File)              ← abstract，纯 Java
  ▼
java.io.UnixFileSystem.list(File)          ← 纯 Java（还有 BlockGuard 检查）
  ▼
java.io.UnixFileSystem.list0(File)         ← **NATIVE**  ← hook 点
  ▼
C++ libcore_io_UnixFileSystem.cpp
  ▼
libc opendir()/readdir()                   ← **libc**  ← GOT patch 点
```

**`File.listFiles()` 本身不是 native** —— 它是纯 Java，反射也 hook 不了（除非改写 dex）。

**所以"hook 最外层"在这条链上不成立** —— 最外层根本没有 native 入口。

---

## 二、决定"一层还是多层"的三条判据

### 判据 1：这一层有没有 native 方法？

| 层 | 类型 | 能否 hook |
| --- | --- | --- |
| `File.listFiles()` | 纯 Java | ❌ 不能 |
| `FileSystem.list()` | abstract Java | ❌ 不能 |
| `UnixFileSystem.list0()` | **native** | ✅ **能（RegisterNatives）** |
| libc `opendir` | native | ✅ 能（GOT patch） |

**结论**：**hook 必须落在"有 native 方法"的那一层**。

### 判据 2：这个 native 点会不会被别的调用者直接用？

| 场景 | 是否需要额外 hook |
| --- | --- |
| 只有 `File.listFiles` 会走到 `list0` | 只 hook `list0` 就够 |
| **app 的 .so 直接调 `opendir`** | **libc 那层也必须 hook**（GOT patch） |
| 别的 Java 代码直接调 `Libcore.os.stat` | `Linux.stat` 那层必须 hook |

**结论**：**每个"独立入口"都要覆盖**。数量取决于"有几条互不相交的入口路径"。

### 判据 3：同一条链上多层同时 hook 会怎样？

**关键前提：翻译是否幂等**。

已实测验证（`tools/analyze/verify-idempotent-translate.py`）：

```
=== 沙箱路径是否会被二次命中（幂等性检查）===
  OK (不命中) .../virtual/data/<instance>/
  OK (不命中) .../virtual/data/<instance>/files/x.mht
  OK (不命中) .../virtual/data/<instance>/external_data/files/x.mht
  OK (不命中) .../virtual/data/<instance>/sdcard/Android/data/mark.via/files/offline/x.mht
  OK (不命中) .../virtual/data/<instance>/host_ext/files/offline/x.mht
  OK (不命中) .../virtual/data/<instance>/obb/x.obb

结论：所有沙箱路径都不会被任何规则再次命中 -> 翻译幂等
```

**所以**：`list0`（Java 层）翻译后再调 `opendir(沙箱路径)`，GOT patch 看到沙箱路径不匹配规则 → **原样透传** → **不会双重翻译** ✅

**但这是"当前规则集"的结论**。**新增任何规则都必须重新验证幂等**（特别是不要加一条能匹配 `/data/user/0/com.nextvm.app/` 的宽规则）。

---

## 三、本项目 `File` 这条链其实**是分叉的**（这是最关键的发现）

用 `dexdump -d` 看 `UnixFileSystem` 每个方法体调了谁：

| `File` 方法 | `UnixFileSystem` 实现 | 最终调 | 走哪条 |
| --- | --- | --- | --- |
| `listFiles` | `list` | **`list0`** | UnixFileSystem 自己的 native |
| `mkdirs` | `createDirectory` | **`createDirectory0`** | 同上 |
| `exists/isDirectory/canRead` | `getBooleanAttributes` | **`getBooleanAttributes0`** | 同上 |
| `lastModified` | `getLastModifiedTime` | **`getLastModifiedTime0`** | 同上 |
| `getUsableSpace` | `getSpace` | **`getSpace0`** | 同上 |
| **`length()`** | `getLength` | **`Os.stat`** → `Linux.stat` | **绕去 Linux** |
| **`delete()`** | `delete` | **`Os.remove`** → `Linux.unlink` | **绕去 Linux** |
| **`renameTo()`** | `rename` | **`Os.rename`** → `Linux.rename` | **绕去 Linux** |

**所以 `File` 这条"一条链"实际上分成两条子链**：

```
File.listFiles/mkdirs/exists/lastModified/getUsableSpace
   → UnixFileSystem.*0          （要 hook 这里）

File.length/delete/renameTo
   → Os.*  →  Linux.*            （要 hook Linux 这里）
```

**"hook 一层就够"在这里不成立** —— 因为 `File` 自己不是 native，而它下面的 native 点**分布在两个类里**。

**这就是为什么必须两层都覆盖：`UnixFileSystem.*0` + `Linux.*`。**

---

## 四、更一般的判据（可复用到任何多层系统）

### 4.1 一个问题就能定

> **"如果我拦住这一层，会不会有另一条路径绕过我到达底层？"**

- 会 → 需要多层（或被绕过的那条也要 hook）
- 不会 → 一层够

### 4.2 三种布局与对策

| 布局 | 特征 | 对策 |
| --- | --- | --- |
| **A. 单链，最外层有 native** | `X.foo()` 直接是 native | **hook 一层** |
| **B. 单链，最外层纯 Java** | `File.foo()` → native 在下面某层 | **hook 那个 native 层**（一层，但是"下层"） |
| **C. 分叉** | `File` 的不同方法走不同 native | **每个分叉点都要 hook** |

**本项目的 `File` 属于 C**，`Os` 属于 B，`libc` 属于 A（对 app .so 而言）。

### 4.3 多层的代价

| 代价 | 说明 |
| --- | --- |
| **双重翻译风险** | 只在"规则能匹配翻译后路径"时发生（已验证当前幂等） |
| **性能** | 每层都有 JNI/字符串比较；`opendir` 是高频 |
| **调试复杂度** | 出问题时难以判断"是哪一层没生效" → 必须每层都有计数（本项目已有 `fs.stats`） |
| **维护** | 每层签名都可能随版本变（见 `docs/设计/设计-JNI签名权威来源与版本跟随.md`） |

---

## 五、结论与建议

### 5.1 答案

> **不是"一层或多层"的二选一，而是：每个「能自己到达底层」的入口都要拦，且必须拦在"有 native 方法"的那一层。**
>
> - 如果某一层本身就是 native（`list0`）→ hook 那一层即可
> - 如果某一层是纯 Java（`File`）→ 必须 hook 它下面的 native 分叉点
> - 同一条链上多层并存是**安全的**，前提是**翻译幂等**（已实测验证）

### 5.2 当前项目的覆盖情况

| 入口 | 拦在哪层 | 状态 |
| --- | --- | --- |
| `File.listFiles` | `UnixFileSystem.list0` | ✅ |
| `File.mkdirs` | `UnixFileSystem.createDirectory0` | ✅ |
| `File.createNewFile` | `UnixFileSystem.createFileExclusively0` | ✅ |
| `File.exists/isDirectory/canRead` | `UnixFileSystem.getBooleanAttributes0` | ✅（本轮补） |
| `File.delete` | `Linux.unlink` | ✅ |
| `File.renameTo` | `Linux.rename` | ✅ |
| **`File.length`** | `Os.stat` → `Linux.stat` | ❌ **Java 层未 hook** |
| `File.lastModified` | `UnixFileSystem.getLastModifiedTime0` | ❌ |
| `File.getUsableSpace` | `UnixFileSystem.getSpace0` | ❌ |
| `File.getCanonicalPath` | `UnixFileSystem.canonicalize0` | ❌ |
| `Os.xxx` | `Linux.*` | ✅ 部分（open/access/mkdir/unlink/rename） |
| NIO2 | `UnixNativeDispatcher.*` | ❌（数据表明可暂缓） |
| app 的 .so | libc（GOT patch） | ✅ |

### 5.3 建议的验证纪律（多层的代价必须被观测）

**每一层都要有自己的计数**，否则"哪层没生效"无法判断：

```
checks.log:
  fs.stats mkdir=t/tl/f opendir=... （native GOT 层）
  javaio.stats open=t/tl/f mkdir=... （Linux 层）
  ufs.stats list0=t/tl/f getBooleanAttributes0=... （UnixFileSystem 层）
```

**当前只有 `fs.stats`（GOT 层）与 `java_io_fs_*`（合并计数）** →
**建议按层拆开**，否则多层架构下排查会退化成猜。

---

## 六、一句话

> **"hook 一层还是多层"取决于「最外层有没有 native 方法」和「是否分叉」。**
> `File` 是纯 Java 且分叉到两个类 → **必须多层**；
> 而多层能安全共存，是因为**翻译幂等**（沙箱路径不命中任何规则）。
> 多层的代价是"哪层没生效"难判断 → **所以每层都要有独立计数**。
