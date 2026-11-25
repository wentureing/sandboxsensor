# 调研：为什么 Java 层难 hook，以及现在该不该 hook

- 日期：2026-11-25
- 依据：本项目在 `baseline-v150` 上的全部实测（v141 → v147 → v148 → 本轮）
- 结论先行：
  1. **Java 层难的本质不是技术难，是「错了不会告诉你」**
  2. **只在「别的层做不到」时才 hook Java 层**
  3. **本项目已 hook 的 5 个 `UnixFileSystem.*0` 是必要的，不是顺手加**

---

## 一、为什么难：十条实证

### 难点 1：编译期零检查

```cpp
JNINativeMethod methods[] = {
    {"mkdir", "(Ljava/lang/String;I)V", (void*) hooked_mkdir},
    //          ↑ 这个字符串写错，编译器、IDE、lint 全都不报
};
```

**对比 native GOT patch**：

```cpp
real_mkdir = (orig_mkdir_t) dlsym(RTLD_DEFAULT, "mkdir");
//                                     ↑ 返回 null 就是错，可以立刻检查
```

**native 层有符号表可验证；Java 层的方法签名只是运行时字符串。**

### 难点 2：批量注册是全或无，且错误信息极少

```cpp
RegisterNatives(cls, methods, 6);   // 6 个里错 1 个 → 返回 -1
```

**实测日志**：
```
E NextVM-Native: installJavaIoHooks: RegisterNatives failed code=-1
```

**没有说哪个方法错、没说为什么错。** 直到改成逐个注册 + `ExceptionDescribe()` 才能定位。

### 难点 3：类名/方法名的"常识"经常是错的

本轮实证：

| 以为 | 实际 |
| --- | --- |
| `libcore.io.Linux.opendir` | **该类没有 opendir**（150 个方法里没有） |
| `File.listFiles` 走 `Linux.opendir` | **走 `java.io.UnixFileSystem.list0`** |
| `java.*` 在 `core-libart.jar` | **在 `core-oj.jar`** |
| `File.mkdirs` 走 `Linux.mkdir` | **走 `UnixFileSystem.createDirectory0`** |

**光看文档/记忆写代码，必然写错。**

### 难点 4：`FindClass` 拿不到隐藏类

```cpp
env->FindClass("libcore/io/ErrnoException");   // 在自建线程里可能返回 null
```

原因：`FindClass` 从"调用它的类"的 ClassLoader 找；`v81-apk-watch` 这类自建线程可能没有正确的 loader。

**后果**（v147 的真实根因）：
- `ErrnoException` 拿不到 → fallback 到 `FileNotFoundException`
- `File.exists()` 的 `checkAccess` 只 catch `ErrnoException`
- **异常逃逸 → `FATAL EXCEPTION: v81-apk-watch`**

### 难点 5：失败是**静默**的

```cpp
private fun installJavaIoHooks(): Boolean {
    ...
    return success;   // ← 返回 false，但调用方没人看
}
```

**实测后果**：`javaIo` 一直是"开着的"，但 hook **从来没装上过**。
而我们在之后几轮里一直以为"hook 生效了，只是语义不对"。

### 难点 6：初始化时序耦合

```
initNativeHooks Phase 1: installJavaIoHooks()      ← real_* 此刻全是 nullptr
initNativeHooks Phase 2: tryLoadNativeLibrary() → install_plt_hooks()  ← 这里才 dlsym
```

**后果**：Java 层 hook 里调 `real_mkdir` 拿到 null → 只能 fallback 到 `::mkdir` → 而那依赖"自己的库不被 GOT patch"这个隐性前提。

### 难点 7：`File` 的 native 点**分叉在两处**

```
File.listFiles    → UnixFileSystem.list0
File.mkdirs       → UnixFileSystem.createDirectory0
File.exists       → UnixFileSystem.getBooleanAttributes0
File.length       → Os.stat  → Linux.stat
File.delete       → Os.remove → Linux.unlink
File.renameTo     → Os.rename → Linux.rename
```

**只 hook `Linux` 或只 hook `UnixFileSystem` 都是"半覆盖"** —— 而半覆盖的表现是"某些操作对了、某些不对"，极难排查。

### 难点 8：诊断通道本身不可靠

| 问题 | 表现 |
| --- | --- |
| guest 进程的 `LOGI` 被设备吞 | 写了看不到 |
| `RegisterNatives` 失败无异常信息 | 只有 `code=-1` |
| `DiagnosticsHolder` 未就绪时 `stage()` 被静默丢弃 | 实测 71ms 规则注册 vs 78ms Holder install |
| `v81-apk-watch` 是自建线程 | 崩溃栈要单独找 |

### 难点 9：注册有时间窗口

`RegisterNatives` 必须在"类已加载、方法还没被 JIT 内联"的窗口内完成。
本项目的做法是在 `initNativeHooks` 早期做，但**如果类还没加载就会失败**。

### 难点 10：JIT 可能绕过（理论风险，未实测）

若某调用点被 AOT 编成直接调 native 符号（而非经 method entry），替换可能不生效。
**判据**：hook 后打计数，应用明明调了但 `total` 不涨 → 就是没生效。

---

## 二、难的本质：一句话

> **native 层错了会崩、会返回 null、`dlsym` 能验；
> Java 层错了**什么都不会发生** —— 方法照常被调用，走的是原实现。**
>
> 所以 Java 层 hook 的问题不是"难写"，而是**"写错了没有反馈"**。

---

## 三、现在该不该 hook：分层决策

### 3.1 决策树

```
这个文件操作用 Java 层 hook 之外的方式能解决吗？
│
├─ 能（走 Context / VirtualContext）
│     → ❌ 不要 hook Java 层
│     例：getExternalFilesDir() 直接返回沙箱路径
│         （via 保存 .mht 成功，靠的就是这条，与 hook 无关）
│
├─ 能（走 native GOT patch）
│     → ❌ 不要 hook Java 层
│     例：app 自己 .so 里的 open/mkdir
│         有 dlsym 可验证，出错能发现
│
└─ 不能（Java 层是唯一入口）
      → ✅ 必须 hook
      例：File.listFiles / mkdirs / exists
          —— 没有别的办法能让它们看见沙箱
```

### 3.2 本项目逐项对照

| 能力 | 能否绕开 Java 层 | 结论 |
| --- | --- | --- |
| `getExternalFilesDir()` | ✅ VirtualContext 直接返回沙箱 | **不 hook**（已正确） |
| app 的 .so 调 libc | ✅ GOT patch | **不 hook**（已覆盖） |
| `File.listFiles()` | ❌ 唯一入口是 `list0` | **必须 hook** ✅ |
| `File.mkdirs()` | ❌ 唯一入口是 `createDirectory0` | **必须 hook** ✅ |
| `File.exists()` | ❌ 唯一入口是 `getBooleanAttributes0` | **必须 hook** ✅ |
| `File.createNewFile()` | ❌ 唯一入口是 `createFileExclusively0` | **必须 hook** ✅ |
| `File.delete()` | ✅ 走 `Os.remove` → `Linux.unlink` | **hook Linux 即可** ✅ |
| `File.renameTo()` | ✅ 走 `Os.rename` → `Linux.rename` | **hook Linux 即可** ✅ |
| `File.length()` | ⚠️ 走 `Os.stat` → `Linux.stat` | **hook Linux.stat 即可**（未做） |
| NIO2 `Files.*` | ❌ 唯一入口是 `UnixNativeDispatcher` | 数据表明 app 几乎不用 → **暂缓** |

**所以：已 hook 的 5 个 `UnixFileSystem.*0` 是"必要"的，不是"顺手"。**

### 3.3 什么情况下**不该**继续加 Java 层 hook

| 情况 | 理由 |
| --- | --- |
| 能通过 `VirtualContext` 返回正确值 | 零 hook 成本，零风险 |
| 能通过改 `File` 之外的 API（如 `ContentResolver`）解决 | 同上 |
| 只是"为了统一"、"顺手也拦一下" | 每多一个注册点就多一个静默失败源 |
| app 几乎不用的 API（如 NIO2，实测 0–4 次 invoke） | 收益极低，风险照旧 |

---

## 四、如果继续 hook，必须同时具备的保障（缺一不可）

按重要性排序，**前 4 条是硬要求**：

| # | 保障 | 现状 |
| --- | --- | --- |
| **1** | **逐个 `RegisterNatives` + 失败日志 + `ExceptionDescribe()`** | ✅ `14b9c7d` |
| **2** | **签名对设备 dex 验证**（不靠记忆/文档） | ✅ `tools/device/jni-sig-audit.py` |
| **3** | **隐藏类用 `Class.forName`** | ✅ `find_class_via_forname` |
| **4** | **失败不静默、不抛错类型** | ✅ 删掉 `FileNotFoundException` fallback |
| 5 | **每层独立计数**（多一层多一个"可能没生效"） | ⚠️ **未做**（三层合并成 `java_io_fs_*`） |
| 6 | **默认关闭 + 分批启用** | ✅ `DebugFlags.JAVA_IO` 默认 false |
| 7 | **真机回归**（探针通过 ≠ 应用可用） | ⚠️ **未跑**（探针 F 组还没跑过） |

**缺口 5 和 7 是当前最该补的**：

- **5**：多层架构下，"哪层没生效"必须能从 `checks.log` 直接读出，而不是靠猜
- **7**：v141 的教训是"探针 W38 成功，Via 却完全不可交互" —— **探针通过不等于应用可用**

---

## 五、结论

### 为什么难

> **因为 Java 层 hook 是"运行时的字符串契约"**：编译器不查、IDE 不查、lint 不查，
> 注册失败只返回 `code=-1`，方法照常被调用（走原实现）而**毫无报错**。

### 是否建议

> **建议"最小必要"**：
> - 能靠 `VirtualContext` 或 native GOT 解决的，**一律不 hook Java 层**
> - **只有 Java 层是唯一入口时才 hook**（`File` 的 list/mkdir/exists/createNewFile）
> - 每加一个注册点，必须同时具备前 4 条保障
> - **不要"顺手"加** —— 每多一个点就多一个静默失败源

### 本项目当前判断

| 结论 | 说明 |
| --- | --- |
| ✅ **已加的 5 个是必要的** | `File` 的 list/mkdir/exists/createNewFile 没有别的入口 |
| ✅ **`File.delete/renameTo` 不用加** | 已由 `Linux.unlink/rename` 覆盖 |
| ⚠️ **`File.length` 应加（`Linux.stat`）** | 走 `Os.stat`，Java 层未 hook |
| ❌ **NIO2 暂不加** | 实测 app 几乎不用（0–4 次 invoke） |
| ❌ **不要为了"统一"再加** | 除非有实测证据表明某 app 需要 |
