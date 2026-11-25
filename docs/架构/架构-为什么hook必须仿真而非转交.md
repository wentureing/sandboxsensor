# 架构：为什么 hook 要"高度仿真"而不是"转交给系统"

- 日期：2026-11-25
- 触发：用户问「为什么我们的 hook 要高度仿真而不是转交给系统」
- 结论先行：
  1. **两种 hook 语义完全不同**：`GOT patch` 是**拦截**（可转发），`RegisterNatives` 是**替换**（原实现被移走）
  2. **`File` 那条路被迫仿真** —— 因为它的委托点 `FileSystem` 是**包私有**，我们插不进去，只能替换 native 方法本身
  3. **`Os` 那条路本可以转交** —— `libcore.io.Os` 是 **public interface**，可以动态代理 → 只翻路径 + 转发，不需要仿真

---

## 一、先分清两件事："文件操作" 与 "包装层行为"

以 `File.mkdirs()` 为例，`createDirectory0` 这个 native 方法在 AOSP 里做了三件事：

```c
Java_java_io_UnixFileSystem_createDirectory0(JNIEnv *env, jobject this, jobject file) {
    jboolean rv = JNI_FALSE;
    WITH_FIELD_PLATFORM_STRING(env, file, ids.path, path) {   // ① 取路径
        if (mkdir(path, 0777) == 0) {                          // ② 调系统
            rv = JNI_TRUE;
        }
    } END_PLATFORM_STRING(env, path);
    return rv;                                                 // ③ 包装结果（失败返回 false）
}
```

| 部分 | 我们怎么做 |
| --- | --- |
| ② **文件操作本身** | **完全转交 libc**（`real_mkdir`）—— 这部分**没有仿真** |
| ① 取路径 + 翻译 | 我们加的（这就是 hook 的目的） |
| ③ **包装层行为**（失败返回 `false` 而非抛异常） | **必须仿真** ← 用户看到的就是这部分 |

**所以"仿真"只发生在第 ③ 步** —— 真正的文件操作用的是系统调用。

---

## 二、为什么第 ③ 步无法省掉：`RegisterNatives` 是"替换"语义

### 2.1 对比 GOT patch（拦截语义）

```cpp
// GOT patch：保存原函数指针，可以转发
real_mkdir = (orig_mkdir_t) dlsym(RTLD_DEFAULT, "mkdir");
int hooked_mkdir(const char* p, mode_t m) {
    return real_mkdir(translate(p), m);   // ← 转交给系统，不需要知道 mkdir 的语义
}
```

**native 层可以"只改参数、其余转交"** —— 因为**原函数还在**（`dlsym` 拿得到）。

### 2.2 `RegisterNatives` 没有"原实现"

```cpp
// RegisterNatives：把 method table 里的 entry 换成我们的函数
env->RegisterNatives(cls, &methods[i], 1);
// 原 Java_java_io_UnixFileSystem_createDirectory0 **被移走，无法调用**
```

**没有 `dlsym` 一样的"拿原实现"机制**（Java native 方法没有导出符号可查）。

**所以**：**替换之后，我们必须自己实现"取路径 + 调系统 + 包装结果"三件事** → 看起来像"高度仿真"。

### 2.3 一句话

> **GOT patch = 拦截 → 能转发 → 不用仿真。
> RegisterNatives = 替换 → 原实现没了 → 必须重写（包括包装层）。**

---

## 三、那能不能"换个位置插入"，避免重写？验证结果

理想方案是**替换"委托点"**而不是"native 方法"：

```
File.mkdirs() → FileSystem.createDirectory() → UnixFileSystem.createDirectory0() → libc
     ↑                    ↑                              ↑
   插这里？            插这里？（最理想）              插这里？（=现在的做法）
```

**"最理想"是把 `File.fs` 换成一个"只翻译路径、其余 `super` 委托"的子类。**
**但这条路被一个客观事实堵住了 —— 实测结果：**

| 类 | Access flags | 结论 |
| --- | --- | --- |
| `java.io.FileSystem` | **ABSTRACT**（**没有 PUBLIC**） | **包私有 → 应用代码无法继承** |
| `java.io.UnixFileSystem` | （无 flag，同样包私有） | 同上 |
| **`libcore.io.Os`** | **PUBLIC INTERFACE ABSTRACT** | **公开接口 → 可以自己实现/代理** ✅ |
| `libcore.io.Libcore` | PUBLIC FINAL | 字段 `os` 可反射替换 ✅ |
| `libcore.io.Linux` | PUBLIC FINAL | 公开但 `final`，不能继承 |

### 结论

| 路径 | 能否"替换委托点" | 说明 |
| --- | --- | --- |
| **`File.*`** | ❌ **不能** | `FileSystem`/`UnixFileSystem` 都是**包私有**，插不进去 → **只能在 native 方法层重写** |
| **`Os.*`** | ✅ **能** | `libcore.io.Os` 是 public interface，`Libcore.os` 是 public 字段 → **动态代理** |

---

## 四、所以两种情况应该用不同策略

### 4.1 `File` 那条路（现状，被迫仿真）

**必须** `RegisterNatives`，**必须**复刻包装层行为：

| 方法 | 必须复刻的行为（AOSP `UnixFileSystem_md.c` / `libcore_io_Linux.cpp`） |
| --- | --- |
| `list0` | `opendir` 失败 → `return NULL`（不抛）；跳过 `.`/`..` |
| `createDirectory0` | `mkdir(path, **0777**)` 失败 → `return JNI_FALSE` |
| `getBooleanAttributes0` | `statMode` 失败 → `return 0`；成功 → `BA_EXISTS|BA_REGULAR|BA_DIRECTORY`（**不含 BA_HIDDEN**） |
| `createFileExclusively0` | 失败 → `return JNI_FALSE` |
| `canonicalize0` | **失败抛 `IOException`**（`JNU_ThrowIOExceptionWithLastError`） |
| `Linux_open` | 空参/失败 → **抛 NPE / ErrnoException**，返回 NULL **但异常已挂起** |

**这就是"仿真"的全部内容** —— 每一项都能在 AOSP 的 C 实现里逐行对照。

### 4.2 `Os` 那条路（**可以改成转交**，建议优化）

```
现在：RegisterNatives 替换 Linux.open/mkdir/... （重写，要仿真）
更好：动态代理替换 Libcore.os，只翻路径 + 转发给原 Os
```

**写法示意**（设计，未实施）：

```java
// Os 是 public interface，可以 Proxy
Os original = Libcore.os;                       // 反射拿原值
Os proxy = (Os) Proxy.newProxyInstance(..., new Class[]{Os.class},
    (p, method, args) -> {
        if (isPathArg(method, args)) args = translatePathArgs(args);
        return method.invoke(original, args);   // ← 转交！不仿真任何行为
    });
Libcore.os = proxy;                             // 反射替换静态字段
```

**优点**：
- **不仿真任何行为**（异常/返回值都由原实现决定）
- **不依赖签名**（方法名/参数变化自动适配）
- **可精确回退**（把 `os` 换回去）

**局限**：
- 覆盖 `Os.*`（`android.system.Os` 的路径），**覆盖不到 `File.*`**（因为 `File` 走 `UnixFileSystem`，不经过 `Libcore.os`）
- 反射改 `public final` 字段需要绕过访问检查
- 每次调用多一层 `Proxy` 开销（`Os` 调用不如 `File` 密集，可接受）

### 4.3 两条路的对比

| 维度 | `File`（RegisterNatives） | `Os`（动态代理） |
| --- | --- | --- |
| 仿真负担 | **高**（要复刻每个方法的包装层） | **零** |
| 版本适配 | 签名/行为都可能变（要审计+对照） | 方法名不变即可 |
| 覆盖率 | `File` 的 99% 调用量 | `Os` 的 1% |
| 可回退 | 需重新 RegisterNatives | 换回原 `os` 即可 |
| 是否可行 | ✅ 唯一可行 | ✅ 可行且更优 |

---

## 五、直接回答"为什么必须仿真"

> **因为 `File` 的委托点 `FileSystem` 是包私有的，我们没有"插进去、只改路径、其余委托"的位置。
> 唯一能插入的地方是 native 方法本身，而 `RegisterNatives` 是替换语义 ——
> 原实现被移走，我们被迫连"失败该返回 false 还是抛异常"这种包装层行为一起重写。**
>
> **这不是设计选择，是 Android 的访问控制（包私有）逼出来的。**
>
> **而 `Os` 那条路本可以不做仿真** —— 它是 public interface，可以用动态代理只翻路径再转发。
> 只是当前实现用了 `RegisterNatives`，把本可"转交"的路径也做成了"重写"。

---

## 六、建议的改进方向

| 优先 | 动作 | 收益 |
| --- | --- | --- |
| **P0** | 把 `Linux.*` 的 hook 从 `RegisterNatives` 改为**动态代理 `Libcore.os`** | **彻底去掉这一族的仿真负担**；版本适配几乎免费 |
| P1 | `File` 那族保持不变（`RegisterNatives`），但**逐项对齐 AOSP 的 C 实现**（已有 `UnixFileSystem_md.c` / `libcore_io_Linux.cpp` 可对照） | 减少行为偏差 |
| P2 | 把 AOSP 源码放进 `tools/ref/`（`UnixFileSystem_md.c` + `libcore_io_Linux.cpp`），后续每次改动都对照 | 有据可依，不再靠推导 |

**注意 P0 的一个前提**：`Linux.*` 里有些方法**不能**靠 `Os` 代理覆盖（比如 `File` 内部直接用 `Libcore.os` 之外的路径），
但 `android.system.Os.*` 那一侧是全覆盖的。要逐方法确认调用者。

---

## 七、本次验证拿到的权威源码（已落盘）

| 文件 | 行数 | 用途 |
| --- | --- | --- |
| `tools/tmp/libcore_io_Linux.cpp` | 2916 | `Linux.*` 的真实实现（`Linux_open` 等） |
| `tools/tmp/UnixFileSystem_md.c` | 572 | `UnixFileSystem.*0` 的真实实现（`list0`/`createDirectory0`/`getBooleanAttributes0`/`canonicalize0`） |

来源：`git clone --depth 1 https://github.com/LineageOS/android_libcore.git`（AOSP libcore 镜像，分支 `lineage-21.0`）
—— **GitHub 上 `aosp-mirror/platform_libcore` 不存在，`LineageOS/android_libcore` 是可用镜像。**

**已据实修正的三处偏差**：
1. `createDirectory0` 的 mode：`0770` → **`0777`**（AOSP 用 0777，umask 再裁剪）
2. `getBooleanAttributes0`：**去掉 `BA_HIDDEN(0x08)`**（AOSP 不返回它，`File.isHidden()` 在 Java 层用 `startsWith(".")`）
3. `Linux_open` 空参分支：返回 null **但已抛 NPE/ErrnoException**（与 AOSP 一致：`ScopedUtfChars` 对 null 抛 NPE）
