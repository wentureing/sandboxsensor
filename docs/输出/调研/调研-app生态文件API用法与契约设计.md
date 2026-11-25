# 调研：app 生态实际怎么调文件 API —— 契约该针对什么，项目现状差在哪

- 日期：2026-11-25
- 方法：pull 5 个真实 app 的 apk → 提取 dex → `dexdump -d` 统计 **`invoke-` 指令数**
  （不是"类名是否出现在字符串池"，而是"是否真的调用"）
- 样本：mark.via（轻量浏览器）、com.deepseek.chat（AI 客户端）、
  io.legado.app.releaseS（Kotlin 阅读器）、org.localsend.localsend_app（跨平台传输）、
  tv.danmaku.bili（B 站）

---

## 一、实测数据（`invoke-` 指令数，首个 dex 抽样）

| app | `File` | FOS | FIS | RAF | `Os` | `Files` | FileChannel | `UnixNativeDispatcher` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mark.via | **1380** | 76 | 77 | 75 | 5 | **0** | 21 | **0** |
| deepseek.chat | **2060** | 92 | 107 | 78 | 6 | 4 | 35 | **0** |
| legado | 1304 | 80 | 113 | 30 | 9 | **0** | 43 | **0** |
| localsend | 508 | 61 | 69 | 14 | **13** | 1 | 20 | **0** |
| bili | 842 | 35 | 55 | 21 | 10 | **0** | 25 | **0** |

（FOS/FIS/RAF = `FileOutputStream` / `FileInputStream` / `RandomAccessFile`）

### 三件事一眼可见

1. **`java.io.File` 是绝对主流**：1380–2060 次，**5/5 个 app 都重度使用**
2. **`Os` 少量但普遍存在**：5–13 次 —— 不能忽略
3. **NIO2 `Files` 几乎不用**：0–4 次；**`UnixNativeDispatcher` 0 次**（app 不碰私有类）

---

## 二、这组数据决定"契约针对什么"

### 2.1 主契约：`java.io.File` 的语义（覆盖 99% 调用量）

所有 app 都通过 `File` → `FileSystem`（抽象）→ `UnixFileSystem.*0`（native）。
**这是杠杆最高的一点**：hook 住 `UnixFileSystem`，5/5 个 app 的主路径就都覆盖了。

### 2.2 次契约：`FileDescriptor` 的语义（`open`/`openat`）

`FileOutputStream`/`FileInputStream`/`RandomAccessFile` 共 61–113 次，
它们都走 `IoBridge.open` → `libcore.io.Linux.open`（或 `openat`）。
**hook `Linux.open`/`openat` 覆盖这一层。**

### 2.3 补充契约：`android.system.Os` 的语义

5–13 次，虽少但真实存在（如 localsend 13 次）。
它走 `Os` → `BlockGuardOs` → `Linux.*`，**hook `Linux.*` 就覆盖**。

### 2.4 可暂缓：NIO2

`Files` 0–4 次、`UnixNativeDispatcher` 0 次 → **当前阶段可以不覆盖**。
真要覆盖时，目标是 `UnixNativeDispatcher.readdir/closedir`（native），
不是 `File` 那条路。

### 2.5 契约的准确表述

> **契约不是"某个类"，而是「`File` 的公开语义」：**
>
> - `File.listFiles()` 返回该目录下真实存在的条目
> - `File.exists()` / `isDirectory()` 反映**容器视角**的真实状态
> - `File.mkdirs()` 在容器视角创建成功即返回 true
> - `File.createNewFile()` / `delete()` / `renameTo()` 同理
>
> **只要这些语义在容器里成立，任何 app 都能正常工作** ——
> 至于底层是 `UnixFileSystem.list0` 还是未来某个新方法，都是实现细节。

---

## 三、当前项目实际在做什么（与契约的差距）

### 3.1 已经做的

| 层 | 现状 |
| --- | --- |
| **L1 `UnixFileSystem`** | 只 hook 了 **3 个**：`list0` / `createDirectory0` / `createFileExclusively0` |
| **L2 `Linux`** | hook 了 `open` / `access` / `mkdir` / `unlink` / `rename`（+ 原有 `stat`/`lstat`/`readlink` 是 native GOT，不是 Java 层） |
| **L3 NIO2** | ❌ 未覆盖（数据表明可暂缓） |
| **L4 libc** | ✅ native GOT patch 覆盖 |

### 3.2 差距（按影响排序）

| # | 缺什么 | 影响 | 严重度 |
| --- | --- | --- | --- |
| **1** | **`UnixFileSystem.getBooleanAttributes0(String)I`** | **`File.exists()` / `isFile()` / `isDirectory()` / `canRead()` / `canWrite()` 全部走它** | **极高** |
| 2 | `UnixFileSystem.getLastModifiedTime0` / `setLastModifiedTime0` | `File.lastModified()` / `setLastModified()` | 中 |
| 3 | `UnixFileSystem.getSpace0` | `getUsableSpace()` 拿到宿主值 → 可能误判"空间不足" | 中 |
| 4 | `UnixFileSystem.canonicalize0` | `getCanonicalPath()` 返回宿主路径 | 中 |
| 5 | `UnixFileSystem.setPermission0` / `setReadOnly0` | `setExecutable` 等 | 低 |
| 6 | `UnixFileSystem.delete` / `rename`（Java 方法，内部调 native） | 需确认内部调什么 | 待查 |

### 3.3 第 1 条为什么是"极高"

```
File.exists()
  → FileSystem.getBooleanAttributes(File)   (java)
    → UnixFileSystem.getBooleanAttributes0(String)   ← NATIVE，我们没 hook
```

**后果**：`exists()` 检查的是**未翻译的宿主路径** → 返回 `false` →
app 认为"文件不存在" → **即使文件真实存在于沙箱，列表/流程也会判定为空或失败**。

**这很可能就是 via「保存成功但离线列表看不到」的另一半原因**：
`list0` 能列出目录（已 hook），但 app 随后对每个条目调 `exists()`/`length()`
→ 未翻译 → 全部判为不存在 → 过滤掉。

### 3.4 另外要注意：`File.delete` / `File.rename` 是 Java 方法

结构扫描显示 `UnixFileSystem.delete` 与 `rename` 标着 `java`（不是 native）。
它们的实现里可能：
- 调 `delete0` / `rename0`（**在 dex 里没看到这两个 native**，所以不是）
- 调 `Libcore.os.unlink` / `Libcore.os.rename` → **走 `Linux.unlink`/`Linux.rename`**（我们已 hook ✅）

**待验证**：用 `dexdump -d` 看 `UnixFileSystem.delete` 的方法体，确认它调谁。
这决定"删除/重命名"是否需要额外 hook。

---

## 四、下一步（按数据驱动排序）

| 优先 | 动作 | 理由 |
| --- | --- | --- |
| **P0** | 补 `UnixFileSystem.getBooleanAttributes0` | 所有 app 的 `exists/isDirectory/length` 都走它；不补则"文件在但看不见" |
| P1 | 验证 `UnixFileSystem.delete`/`rename` 的方法体，确认是否走 `Linux.*` | 决定是否需要额外 hook |
| P2 | 补 `getLastModifiedTime0` / `getSpace0` | 中影响 |
| P3 | 补 `canonicalize0` | 防路径逃逸 |
| P4 | NIO2（`UnixNativeDispatcher`） | 数据表明可暂缓 |

---

## 五、方法论沉淀

### 5.1 "调查 app"要统计什么

| ❌ 不够 | ✅ 有效 |
| --- | --- |
| 类名出现在字符串池 | **`invoke-` 指令数**（真的调用了） |
| 看单个 app | **多 app 横向对比**（看谁在用、用多少） |
| 只看有没有用 | 看**层次分布**（L1 vs L2 vs L3） |

### 5.2 脚本

`tools/tmp/app-api-survey.py`：pull → 提 dex → 统计字符串引用 + `invoke` 数 → 横向表。
**以后换 app 或用例变化时，重跑一次就能知道该覆盖哪层。**

### 5.3 一句话

> **数据说：`File` 是 99%，`Os` 是 1%，NIO2 约等于 0。**
> 所以契约应当围绕 **`File` 的公开语义**设计，
> 实现上把 **`UnixFileSystem` 的 native 方法**补齐（尤其 `getBooleanAttributes0`），
> 而不是继续在 `libcore.io.Linux` 上堆。
