# 架构：为什么 native hook 救不了 Java 层的文件操作

- 日期：2026-11-25
- 触发：本轮实测 —— 保存的网页**确实落到沙箱**，但 `mkdir` 仍落宿主、`listFiles` 仍看不到
- 结论：**native GOT hook 只覆盖应用 .so 的 libc 调用；Java 层的 `File` API 走 libjavacore，不在 patch 范围**
- 影响：本轮的 `mkdir`/`opendir`/`rename`/`unlink` hook **对 Java 层完全无效**

---

## 一、实测证据（本轮装机，versionCode=82）

### 1.1 保存成功且落沙箱

```
<instance>/sdcard/Android/data/mark.via/files/offline/
  搜索 - Microsoft 必应.mht   82708 B   12:00
  百度一下.mht               565860 B   11:59

宿主 /storage/emulated/0/Android/data/com.nextvm.app/files/  无文件 ✓
```

### 1.2 但 `mkdir` 仍逃逸

```
宿主 .../data/com.nextvm.app/files/
  filters/ font/ gm/ logs/ offline/ proxy/ syncing/ tabs/   ← 全部 11:59 新建
```

**11:59 = 本次 via 启动时刻** → 这些目录是**这次新建的**，不是旧残留。

### 1.3 `opendir`/`mkdir` 的 LOGD 一条都没有

```
grep "opendir: |mkdir: |rename: |unlink: " → 空
```

（部分原因是 guest 的 LOGD 被吞，但结合 1.2 可确认 hook 根本没被调用）

---

## 二、根因：patch 范围不含系统库

实测日志：

```
GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0): /system/lib64/libvivo_runtime.so
GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0): /system_ext/lib64/...
GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0): /apex/com.android.tethering/...
```

**即**：`install_hooks_callback`（`native-hook.cpp` L2061）对 `/system`、`/system_ext`、`/apex`
下的库**一律跳过**，只 patch 应用自己加载的 .so。

### 两条完全不同的调用路径

| 操作 | 调用链 | 经过谁的 GOT | 能否拦截 |
| --- | --- | --- | --- |
| 应用 .so 里直接调 `open()` | app.so → PLT → libc | **应用 .so 的 GOT** | ✅ **能** |
| `FileOutputStream.write()` | Java → **libjavacore.so** → PLT → libc `open` | **libjavacore 的 GOT（系统库）** | ❌ **不能** |
| `File.mkdirs()` | Java → libjavacore → libc `mkdir` | 同上 | ❌ **不能** |
| `File.listFiles()` | Java → libjavacore → libc `opendir` | 同上 | ❌ **不能** |
| `File.renameTo()` | Java → libjavacore → libc `rename` | 同上 | ❌ **不能** |

**所以本轮新加的 4 个 hook，对 via 这类纯 Java 应用等于没加。**

---

## 三、那为什么保存的文件能落沙箱？

**不是 hook 的功劳，是 `VirtualContext` 的功劳**：

```kotlin
// VirtualContext.kt L171-178
override fun getExternalFilesDir(type: String?): File {
    val dir = sandboxManager.getExternalAppFilesDir(instanceId, guestPackageName, type)
    return dir          // ← 直接返回沙箱路径
}
```

via 调 `getExternalFilesDir("offline")` → 拿到**沙箱绝对路径** →
再 `new FileOutputStream(那个路径)` → **本来就在沙箱** → 无需重定向。

**而 `mkdir` 落宿主**，是因为 via **另一处**用 `getOpPackageName()` 拼了宿主路径：

```
Environment.getExternalStorageDirectory() + "/Android/data/" + getOpPackageName() + "/files/..."
= /storage/emulated/0/Android/data/com.nextvm.app/files/...
```

这条路 **Java 层发起** → 需要 hook 才能改 → **但 hook 不到**。

---

## 四、结论：要覆盖 Java 层，只有一条路

| 方案 | 能否覆盖 Java 层 | 说明 |
| --- | --- | --- |
| native GOT patch | ❌ | 系统库不 patch（渲染修复的"只 patch 应用库"就是为此） |
| **`RegisterNatives` 替换 `libcore.io.Linux`** | ✅ | **唯一可行**，即 `JavaIoHooks` 那条路 |
| 改 `VirtualContext` | ⚠️ 部分 | 只覆盖经由 Context 的调用；`getOpPackageName()` 拼路径的覆盖不到 |
| 改 `getOpPackageName()` 返回值 | ❌ | 会破坏 appops 归因（`AppOpsManager.checkPackage` 抛 SecurityException） |

**这就是 v141 引入 `JavaIoHooks`、v147 因语义不一致关闭、v148 修语义但仍默认关闭的原因。**

---

## 五、JavaIoHooks 要覆盖哪些方法（`libcore.io.Linux`）

| 方法 | 对应 | 优先级 |
| --- | --- | --- |
| `open(String, int, int)` | `FileOutputStream` / `RandomAccessFile` | ✅ 已有 |
| `access(String, int)` | `File.canRead/canWrite/exists` | ✅ 已有 |
| **`mkdir(String, int)`** | **`File.mkdir/mkdirs`** | **必须补** |
| **`opendir(String)`** | **`File.list/listFiles`** | **必须补** |
| **`rename(String, String)`** | **`File.renameTo`** | **必须补** |
| **`unlink(String)`** | **`File.delete`** | 补（用户说暂不追求删除安全，但不补会删到宿主） |
| `stat(String)` / `lstat(String)` | `File.exists/length/isDirectory` | 建议补（否则 `exists` 判宿主） |
| `readlink(String)` | `File.getCanonicalPath` | 建议补 |

**AOSP 语义（必须严格对齐，v147 就是这里翻的车）**：

| 情形 | AOSP 行为 |
| --- | --- |
| 失败 | **抛 `libcore.io.ErrnoException`**（不是 `FileNotFoundException`，不是返回 `false`） |
| `mkdir` 成功 | 返回 `Unit`（void） |
| `opendir` 成功 | 返回 `Long`（DIR* 指针的 long） |
| `unlink` 成功 | 返回 `Unit` |
| `rename` 成功 | 返回 `Unit` |
| EINTR | **必须重试**（`TEMP_FAILURE_RETRY`） |
| 参数校验 | `path == null` → `NullPointerException`（AOSP 行为） |

**v147 的三个错误（不能重复）**：
1. `open` 失败抛 `FileNotFoundException` → 应为 `ErrnoException`
2. `access` 失败返回 `false` → 应抛 `ErrnoException`
3. 缺 EINTR 重试

---

## 六、下一轮最小改动清单（供决策）

| # | 改动 | 风险 |
| --- | --- | --- |
| 1 | `install_java_io_hooks` 的 `JNINativeMethod` 表从 2 个扩到 8 个（加 `mkdir`/`opendir`/`rename`/`unlink`/`stat`/`lstat`） | 低（沿用既有实现骨架） |
| 2 | 每个新增方法**严格照 AOSP 的返回值与异常类型**写 | **中**（v147 的教训） |
| 3 | 保持 `DebugFlags.JAVA_IO` 默认 **false**，先用 `setprop debug.nextvm.javaIo 1` 单独验证 | 低（可回退） |
| 4 | 用 `JavaIoHooks.nativeGetStats()`（已有）看 `total/translated/fail` | 低 |

**验证顺序**（每步都停）：
1. 只开 `mkdir` 一个 → 看宿主 8 个目录是否不再新建
2. 只开 `opendir` → 看 via 离线列表是否能看到条目
3. 两个都稳了，再开 `rename`/`unlink`

---

## 七、关于「启用原有 199 处埋点」

用户已允许启用。做法（不需要改 199 处）：

| 项 | 做法 |
| --- | --- |
| 入口 | `GuestBreadcrumb.log()` —— 所有埋点都走这一个函数 |
| 启用 | 本来就一直在记录（`attachFile` 后写 `breadcrumb-<pid>.log`） |
| 增强 | 若要让它也进 `checks.log`，只需在 `GuestBreadcrumb.log()` 尾部加一行 `DiagnosticsHolder.stage("crumb", ...)` |

**注意**：199 处埋点的**验证责任在上游**（用户明确"验证不是我编码的"）——
我们只是**启用/桥接**它，不为它的正确性负责。
