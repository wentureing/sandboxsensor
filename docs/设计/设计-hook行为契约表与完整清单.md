# 复盘：为什么我一直在补边界条件，以及完整的 hook 清单

- 日期：2026-11-25
- 触发：用户两个尖锐问题：
  1. 为什么仿佛在反复补充边界条件，而不是一次完善 hook 相关代码
  2. 强烈建议还需要 hook 什么

---

## 第一部分：为什么反复补边界（我的方法论错误）

### 1.1 我实际做的是什么

| 轮次 | 现象 | 我修了什么 |
| --- | --- | --- |
| 1 | `RegisterNatives failed code=-1` | 逐个注册（为了定位） |
| 2 | 定位到 `opendir` 不在 `Linux` | 删掉 `opendir` |
| 3 | 发现 `File` 走 `UnixFileSystem` | 加 3 个 `*0` |
| 4 | `File.exists` 走 `getBooleanAttributes0` | 加第 4 个 |
| 5 | **崩溃：`close(null)` NPE** | 修 `open` 的 null 返回 |

**每一次都是"遇到一个现象 → 修一处"。**

### 1.2 为什么这样必然反复

**因为我只验证了"签名对不对"，没验证"行为对不对"。**

| 验证维度 | 我做了吗 | 工具 |
| --- | --- | --- |
| 方法存在吗？签名对吗？ | ✅ 做了 | `jni-sig-audit.py`（dexdump 比对） |
| **行为与 AOSP 一致吗？** | ❌ **没做** | **无** |

**签名审计只能回答"这个方法是 `(String,int)V` 吗"，
回答不了"它失败时该抛异常还是返回 false"。**

而**几乎每一次崩溃都出在行为上，不是签名上**：
- v147：`access` 失败返回 `false`（该抛异常）
- v147：`open` 失败抛 `FileNotFoundException`（该抛 `ErrnoException`）
- 本轮：`open` 的空参分支返回 `null`（该抛 `NPE`/`ErrnoException`）

### 1.3 正确的方法：**从「调用方假设」反推契约**

崩溃不是随机的 —— 每个都违反了一条**调用方的隐式假设**：

```java
// IoUtils.canOpenReadOnly（AOSP）
FileDescriptor fd = Libcore.os.open(path, O_RDONLY, 0);
try { ... } finally { Libcore.os.close(fd); }
//                    ^^^^^^^^^^^^^^^^^^^^^^^^ 隐含假设：fd 永远非 null
```

**这个假设在 AOSP 的 `Linux.open` 实现里成立**（失败必抛异常），
**在我们的实现里不成立**（有空参分支返回 null）→ 崩溃。

**所以正确的方法论是**：

> **对每个要 hook 的 native 方法，先列出「所有调用方」，逐个看它们对返回值/异常做了什么假设。
> 这些假设的并集，就是必须满足的行为契约。全部满足之后才动代码。**

**这比"照 AOSP 源码抄一遍"更可靠** —— 因为：
- AOSP 的 C++ 实现拿不到（设备上只有 dex）
- 而且即使拿到，厂商可能改过
- **但调用方的假设是确定的**（就在 dex 里）

### 1.4 我的错误可以这样归纳

| ❌ 我的做法 | ✅ 应该的做法 |
| --- | --- |
| 照 Java 声明写签名 | 照 Java 声明写签名（**这个我做了，用工具**） |
| **遇到崩溃就补一处** | **先枚举所有调用方假设，一次把全部分支写对** |
| 只考虑"正常路径" | 每个方法要有：**空参 / 空路径 / 翻译失败 / syscall 失败 / syscall 成功失败** 全分支 |
| 用"崩不崩"当判据 | 用"调用方假设是否被满足"当判据 |

### 1.5 为什么"一次完善"这次也没做到（诚实说明）

我在 `d0d9e3d` 里修的还只是 `open`/`access` 两个方法的空参分支。
**`mkdir`/`unlink`/`rename`/`list0`/`createDirectory0`/`getBooleanAttributes0` 的空参分支同样没系统检查过。**

**下面第二部分就是该做的事：一张完整的契约表。**

---

## 第二部分：完整的行为契约表（两大契约族）

### 2.1 核心发现：存在两族互斥的契约

| 契约族 | 方法 | 失败时 |
| --- | --- | --- |
| **A 族（libcore.io.Linux.\*）** | `open` `access` `mkdir` `unlink` `rename` `stat` ... | **抛 `ErrnoException`** |
| **B 族（UnixFileSystem.\*0）** | `list0` `createDirectory0` `createFileExclusively0` `getBooleanAttributes0` ... | **返回 `null` / `false` / `0`** |

> **把 B 族的写法用在 A 族上 → 调用方误判 → 崩。
> v147 到本轮的多次崩溃，本质都是这一条。**

### 2.2 A 族逐方法契约

| 方法 | 空参 | 失败 | 成功 | 已知调用方假设 |
| --- | --- | --- | --- | --- |
| `open(String,int,int)` | **抛 NPE** | **抛 ErrnoException** | 返回非 null `FileDescriptor` | `IoUtils.canOpenReadOnly` **假定非 null**；`IoBridge.open` 直接透传 |
| `access(String,int)` | **抛 NPE** | **抛 ErrnoException** | 返回 `true` | `UnixFileSystem.checkAccess` 靠 `catch(ErrnoException)` 得 false |
| `mkdir(String,int)` | 抛 NPE | 抛 ErrnoException | `void` | `Libcore.os.mkdir` 透传 |
| `unlink(String)` | 抛 NPE | 抛 ErrnoException | `void` | `UnixFileSystem.delete` 靠 catch 转 false |
| `rename(String,String)` | 任一 null 抛 NPE | 抛 ErrnoException | `void` | `UnixFileSystem.rename` 靠 catch 转 false |
| `stat(String)` | 抛 NPE | 抛 ErrnoException | 返回 `StructStat` | `File.length()` 走它 |
| `lstat(String)` | 同上 | 同上 | 同上 | 同上 |
| `readlink(String)` | 同上 | 同上 | 返回 `String` | `File.getCanonicalPath` 部分路径 |

**统一要求**：**EINTR 重试**、**绝不返回 null/false 让调用方误判**。

### 2.3 B 族逐方法契约

| 方法 | 空参 | 失败 | 成功 |
| --- | --- | --- | --- |
| `list0(File)` | 返回 `null` | **返回 `null`** | 返回 `String[]` |
| `createDirectory0(File)` | 返回 `false` | **返回 `false`** | `true` |
| `createFileExclusively0(String)` | 返回 `false` | **返回 `false`** | `true` |
| `getBooleanAttributes0(String)` | 返回 `0` | **返回 `0`** | 位掩码 int |
| `getLastModifiedTime0(File)` | 返回 `0L` | 返回 `0L` | 毫秒 |
| `setLastModifiedTime0(File,J)` | 返回 `false` | 返回 `false` | `true` |
| `getSpace0(File,int)` | 返回 `0L` | 返回 `0L` | 字节数 |
| `canonicalize0(String,bool)` | 返回 `null` | 返回 `null` | 规范路径 |
| `setPermission0(File,int,bool,bool)` | 返回 `false` | 返回 `false` | `true` |

**统一要求**：**不抛异常**（调用方没有 try/catch）。

---

## 第三部分：完整 hook 清单（还差什么）

### 3.1 已 hook（本项目当前）

| 层 | 方法 | 契约族 | 空参分支是否已修 |
| --- | --- | --- | --- |
| `Linux` | `open` | A | ✅ `d0d9e3d` |
| `Linux` | `access` | A | ✅ `d0d9e3d` |
| `Linux` | `mkdir` | A | ❌ **未检查** |
| `Linux` | `unlink` | A | ❌ **未检查** |
| `Linux` | `rename` | A | ❌ **未检查** |
| `UnixFileSystem` | `list0` | B | 返回 null ✓ 符合契约 |
| `UnixFileSystem` | `createDirectory0` | B | 返回 false ✓ |
| `UnixFileSystem` | `createFileExclusively0` | B | 返回 false ✓ |
| `UnixFileSystem` | `getBooleanAttributes0` | B | 返回 0 ✓ |

**→ `mkdir`/`unlink`/`rename` 的空参分支必须补（A 族要求抛 NPE）**

### 3.2 强烈建议补的（按影响排序）

| 优先 | 方法 | 族 | 为什么 |
| --- | --- | --- | --- |
| **P0** | `Linux.stat` / `Linux.lstat` | A | **`File.length()` 走 `Os.stat`** → 不补则文件长度读到宿主（用于判断"文件是否非空"的应用会误判） |
| **P0** | `UnixFileSystem.getLastModifiedTime0` | B | `File.lastModified()`；很多 app 用时间排序/判新旧 |
| **P1** | `UnixFileSystem.getSpace0` | B | `getUsableSpace()` 拿宿主值 → 可能误判"空间不足"拒绝保存 |
| **P1** | `UnixFileSystem.canonicalize0` | B | `getCanonicalPath()` 返回宿主路径 → 后续用它拼路径会逃逸 |
| **P2** | `Linux.readlink` | A | `Os.readlink`，部分 app 读符号链接 |
| **P2** | `UnixFileSystem.setPermission0` / `setReadOnly0` | B | `setExecutable` 等；误判权限导致逻辑错 |
| **P3** | `Linux.chmod` / `chown` | A | 少数 app 改权限 |
| **P3** | `Linux.ftruncate` | A | 截断文件 |
| **可暂缓** | `UnixNativeDispatcher.*`（NIO2） | — | 实测 app 0–4 次 invoke；`Files.*` 会走它，但用得少 |
| **不要 hook** | `UnixFileSystem.initIDs` / `getNameMax0` | — | 前者是初始化，后者是常量 |

### 3.3 不需要 hook 的（已在别处覆盖）

| 能力 | 已被谁覆盖 |
| --- | --- |
| `File.delete` | `Linux.unlink`（已 hook） |
| `File.renameTo` | `Linux.rename`（已 hook） |
| `FileInputStream` / `FileOutputStream` / `RandomAccessFile` / `FileChannel` | `IoBridge.open` → `Linux.open`（已 hook） |
| `getExternalFilesDir()` 等 Context 路径 | `VirtualContext` 直接返回沙箱（无需 hook） |
| app 自带 .so 的 libc 调用 | native GOT patch |

---

## 第四部分：建议的执行方式（避免再次"挤牙膏"）

### 4.1 一次性做完的清单

```
阶段 1（A 族补齐空参分支 + EINTR）
  Linux.mkdir / unlink / rename —— 补 path==null 抛 NPE
  （open/access 已修）

阶段 2（P0 新增）
  Linux.stat / Linux.lstat —— 新增（A 族：抛 ErrnoException，返回 StructStat）
  UnixFileSystem.getLastModifiedTime0 —— 新增（B 族：返回 0L）

阶段 3（P1）
  UnixFileSystem.getSpace0 / canonicalize0

阶段 4（P2/P3，按需）
  Linux.readlink / chmod / chown / ftruncate
  UnixFileSystem.setPermission0 / setReadOnly0
```

### 4.2 每个阶段必须带的验证

| 验证 | 方法 |
| --- | --- |
| 签名 | `jni-sig-audit.py`（已就绪） |
| **行为契约** | **新增：`derive-contracts.py` 列出调用方假设，逐条对照** |
| 注册 | `checks.log` 里的 `X/X registered` |
| 真机 | 探针 + 真实 app |

### 4.3 关键结论

> **不能再用"崩了再补"的方式推进。**
> 每一步之前，先用 `derive-contracts.py` 把该方法的**所有调用方假设**列出来，
> 一次性实现全部分支，然后才提交。
>
> **签名审计解决"方法在不在"，契约审计解决"行为对不对" —— 两者缺一不可。**
