# 架构：Java 层路径重定向 —— 施工方向与方案

- 日期：2026-11-24
- 背景：WebView 的 `file://` MIME 问题最终卡在「假 dataDir 方案需要 Java 层重定向」上。
  回退 v137/v139/v140 后（保留 v138），本文把「原有代码里已经留下的施工方向」
  和「技术上怎么补」一次写清。
- 结论先给：**不需要 ART hook，也不需要引入新框架**。项目已有的
  `RegisterNatives` + `translatePath()` 两件东西就够，缺的只是**没人把它们接上**。

---

## 一、现状：三层机制，Java 层是空的

| 层 | 是否存在 | 覆盖 |
| --- | --- | --- |
| L-Java：`VirtualContext` 路径改写 | ✅ | 只覆盖「通过 Context 拿路径」的代码 |
| L-native：GOT/PLT patch | ✅ | 只覆盖被 patch 的 so 里的 libc 导入符号 |
| **L-Java：`java.io` 的 I/O 重定向** | ❌ **空** | —— |

证据（`NativeHookBridge.kt:710-724`）：

```kotlin
private fun hookFileClass(): Boolean {
    // We can't directly hook File constructors in pure Java,
    // but we can intercept the resolution at higher levels.
    // For Phase 1, the VirtualContext and ContentResolverProxy
    // handle most Java-level file access.
    Timber.tag(TAG).d("File class hook points prepared")
    return true          // ← 什么也没装
}
```

实测印证：探针 W19 用 Java `FileOutputStream("/sdcard/wvprobe19.html")` 写文件 →
`EPERM`。因为 `/sdcard` 的重定向规则只在 native 层，**Java 层直通真实 /sdcard**。

---

## 二、原有代码留下的三处施工方向（都不用新写）

### 2.1 `NativeHookBridge.translatePath()` —— **已实现**

`NativeHookBridge.kt:604`：

```kotlin
fun translatePath(originalPath: String): String {
    if (hiddenPaths.contains(originalPath)) return "/dev/null"
    var bestMatch = ""; var bestRedirect = ""
    for ((from, to) in pathRedirections) {          // 最长前缀匹配
        if (originalPath.startsWith(from) && from.length > bestMatch.length) { ... }
    }
    return if (bestMatch.isNotEmpty()) originalPath.replace(bestMatch, bestRedirect) else originalPath
}
```

**这就是「guest 路径 → 真实路径」的翻译函数，已完整实现**（与 native 的 `redirect_path` 语义一致）。

### 2.2 `FileAccessInterceptor` —— **已实现**

`NativeHookBridge.kt:904-999`，包含：

| 方法 | 作用 |
| --- | --- |
| `interceptFile(path)` | translate 后返回新 `File` |
| `interceptFileInputStream(path)` | translate + **fake content**（伪造文件内容） |
| `interceptFileOutputStream(path, append)` | translate + 自动 `mkdirs` 父目录 |
| `interceptExists / interceptCanRead` | translate + `hidePath` 过滤 |
| `interceptListFiles(path)` | translate + 过滤隐藏项 |

**逻辑全都写好了，但没有任何调用者** —— 因为没有"入口"把 `java.io` 的构造/方法接到它上面。

### 2.3 `installNativeLoadHook()` —— **RegisterNatives 的现成范式**

`native-hook.cpp:6318-6380`：

```cpp
// 1. Find java.lang.Runtime
// 2. Save original nativeLoad function pointer via GetMethodID + JNI internal lookup
// 3. Register our hooked version via RegisterNatives
```

项目**已经在用 `RegisterNatives` 替换 `Runtime.nativeLoad`**，并且有完整的
「拿 jclass → 存原函数指针 → 注册新实现 → 转发」套路。

---

## 三、技术方案：用 RegisterNatives 替换 `libcore.io.Linux` 的 native 方法

### 3.1 为什么是 `libcore.io.Linux`

Android 的 Java 文件 I/O 链路：

```
java.io.FileInputStream  →  libcore.io.IoBridge.open()  →  Libcore.os.open()  →  libcore.io.Linux.open()  [native]
java.io.File.exists()    →  UnixFileSystem.getBooleanAttributes0()  →  Linux.stat()  [native]
```

**所有 Java 层的 open/stat/access 最终都落到 `libcore.io.Linux` 的 native 方法上**（实测签名，来自设备 `core-libart.jar`）：

| 方法 | shorty | 签名 |
| --- | --- | --- |
| `open` | `LLII` | `(String path, int flags, int mode) → FileDescriptor` |
| `stat` | `LL` | `(String path) → StructStat` |
| `lstat` | `LL` | 同 |
| `access` | `ZLI` | `(String path, int mode) → boolean` |
| `readlink` | `LL` | `(String path) → String` |
| `unlink` | `VL` | `(String path) → void` |
| `rename` | `VLL` | `(String, String) → void` |
| `mkdir` | `VLI` | `(String, int) → void` |

（`Linux` 类共 **134 个 native 方法**，其中文件相关的是上表这些。）

### 3.2 施工步骤

1. **native 侧**（`native-hook.cpp`，照 `installNativeLoadHook` 的写法）：
   ```cpp
   jclass linuxCls = env->FindClass("libcore/io/Linux");
   // 保存原实现：从 libjavacore.so dlsym（若不可用，直接用 libc 的 openat 自己构造）
   // RegisterNatives 注册我们的 hooked_Linux_open 等
   ```
2. **替换实现里**：先调 `translatePath()`（Kotlin，通过 JNI 回调或把规则表镜像到 native 的 `g_path_redirects` —— 后者更简单，规则本来就在 native 表里）
3. **转发**：用真实 libc `openat(AT_FDCWD, translated, flags, mode)`，再用 `FileDescriptor.setInt$` 构造返回对象

**关键简化**：native 侧的 `g_path_redirects` 与 Kotlin 的 `pathRedirections` 是同一份规则的两个副本 —— 直接在 native 里用 `redirect_path()` 即可，**不需要 JNI 回调回 Kotlin**。

### 3.3 为什么不违反「libart.so 永不 patch」

`RegisterNatives` 是 **ART 公开的 JNI API**：它把 JNI 方法的 native entry 指向我们的函数，
**不修改 libart 的代码段**，也不做 inline hook。README 里那条约束针对的是
「inline hook / patch libart」；`installNativeLoadHook` 已经证明了这条路是被项目接受的。

### 3.4 性能

| 项 | 数值 |
| --- | --- |
| 单次开销 | 一次 `RegisterNatives` 转发（JNI 进 + 字符串比较 + libc 调用），约 **1–3 μs**（比 GOT patch 的 50–200 ns 重，但仍是微秒级） |
| 频次 | 应用启动期几百~几千次文件操作；运行期低频 |
| 影响 | 应用冷启动可能多几毫秒（万次 × 2μs = 20ms 量级），可接受 |

### 3.5 风险（这是全项目最高风险的一类改动）

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | **覆盖 boot classpath 的 native 方法，实现有 bug 会影响整个进程的所有 Java 文件 I/O** | 开关 + 只对指定 guest 启用 + 先做"只打日志不翻译"的空转验证 |
| 2 | 原实现拿不到（libjavacore 不导出符号） | 不转发原实现，直接自己调 libc（`openat`/`fstatat`）构造返回对象 |
| 3 | 返回对象构造（`FileDescriptor`、`StructStat`）复杂 | `StructStat` 用 `Libcore.os.lstat` 的结果类型 —— 可先做"只翻译路径、其余照旧"的最小版 |
| 4 | 与 native GOT hook 重复转换（同一个 open 被转两次） | `redirect_path()` 是幂等的（真实路径不再命中规则）；但需加断言日志验证 |

---

## 四、开源项目能借鉴什么 / 不能借什么

| 项目 | 做法 | 对我们 |
| --- | --- | --- |
| **VirtualApp / Bcore / BlackBox** | **只有 native GOT patch**（I/O redirection 文档也是讲 native 层）+ `VirtualContext` | **同样缺 Java 层**，不能借；但它证明了"只靠 GOT patch 也能跑大多数应用"——我们遇到的是 WebView 这种"外部消费者" |
| **LSPlant / Pine** | ART **方法级 hook**（可 hook `java.io` 的 Java 方法） | 能做到但代价大：patch libart（违反 README 约束）+ 许可证（LGPL/Apache）+ 版本适配 |
| **Xposed 系 FileHook** | 基于 ART hook 替换 `File` 方法 | 同上 |
| **Shadow（腾讯）** | ClassLoader 隔离，不做路径重定向 | 路线不同 |
| **专门用 RegisterNatives 做路径重定向的开源项目** | **没找到** | 这是个空位；但技术上是标准 JNI 用法，风险可控 |

**结论**：没有可直接引入的开源方案；**项目自己的三件半成品已经够用**，
缺的是「把 RegisterNatives 用到 `libcore.io.Linux` 上」这一步。

---

## 五、建议的落地顺序

| 顺 | 动作 | 目的 | 产出判据 |
| --- | --- | --- | --- |
| 1 | `RegisterNatives` 替换 `Linux.open`，**只打日志不翻译** | 验证能否拿到 jclass/方法、能否安全转发 | 应用正常运行 + 日志里能看到所有 open 调用 |
| 2 | 加 `translatePath` 逻辑（用 native 的 `redirect_path`） | 真正的 Java 层重定向 | 探针 W19（写 /sdcard）不再 EPERM |
| 3 | 扩到 `stat/access/lstat/readlink/unlink/rename/mkdir` | 完整覆盖 | 应用文件操作全部走沙箱 |
| 4 | 有了 Java 层重定向后，再回头看 WebView 的假 dataDir | 那时 dataDir 才真正"可用" | W2 是否变 `text/html` |
| 5 | 给 WebView 做**宿主代理**（shouldInterceptRequest，W11 已验证）作为另一条互不冲突的路 | 不依赖 dataDir | 需解决注入时序 |

> **注意**：即使第 1–3 步全部完成，WebView 的 MIME 问题**仍需要第 4 或第 5 步**。
> Java 层重定向解决的是「假路径能不能读」，不是「WebView 怎么判 MIME」。
> 但它是**架构级的地基**：有了它，pathFidelity、假 dataDir、MediaPlayer/PDF 等
> 所有"外部消费者"问题都有了统一的解法，而不是逐个打补丁。
