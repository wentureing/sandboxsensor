# `core/hook/JavaIoHooks.kt`

> 镜像文档（规则 7）。对应代码：`core/hook/src/main/java/com/nextvm/core/hook/JavaIoHooks.kt`
> 拆分自 `NativeHookBridge.kt`（v149）。

## 职责

**做什么**
- 用 `RegisterNatives` 替换 `libcore.io.Linux` 的 `open` / `access`，
  让 **Java 层**（`FileInputStream` / `FileOutputStream` / `FileChannel` …）的文件 I/O
  也走容器的路径重定向。
- 提供安装结果、调用统计、最近异常记录的**诊断出口**。
- 管理两个开关：`debug.nextvm.javaIo`（是否安装）、`debug.nextvm.javaIoTranslate`（是否翻译）。

**不做什么**
- ❌ native 的 GOT/PLT patch —— 那是 `NativeHookBridge` 的职责。
- ❌ 路径规则表的维护 —— 规则由 `NativeHookBridge.setupAppRedirections` /
  `setupExternalStorageRedirections` / `addPathRedirection` 写入 native 的
  `g_path_redirects`；本类只**消费**它。
- ❌ 进程判断 —— 调用方 `NativeHookBridge.initNativeHooks()` 已在主进程提前 return。
- ❌ 线程/进程守卫 —— 本类假定只在 guest 进程被调用。

## 函数

| 签名 | 作用 | 副作用 / 前提 |
| --- | --- | --- |
| `install(): Boolean` | 按开关安装 hook，返回是否成功 | **前提**：native 库已加载、路径规则已注册。会写 `debug-trace.log`（`javaIo installed=…`） |
| `stats(): String?` | `total=… fail=… translated=… translate=…` | 未安装/符号缺失时返回 null（不抛） |
| `recent(): String?` | 最近 ≤32 条「失败 / 发生翻译」的 `#seq op err=… flags=… path` 多行文本 | 同上 |
| `reset()` | 清零统计与最近记录 | 每次 guest 启动调一次，便于"按次观察" |
| `installed: Boolean` | 是否已安装（只读） | 供探针/诊断读 |

**JNI 绑定**（符号名必须与 `native-hook.cpp` 一致，改名字要同步两边）

| Kotlin | C++ 符号 |
| --- | --- |
| `nativeInstall(translate)` | `Java_com_nextvm_core_hook_JavaIoHooks_nativeInstall` |
| `nativeGetStats()` | `…_nativeGetStats` |
| `nativeResetStats()` | `…_nativeResetStats` |
| `nativeGetRecent()` | `…_nativeGetRecent` |
| `nativeClearRecent()` | `…_nativeClearRecent` |

## 偏差登记

| # | 偏差 | 相对谁 | 类型 | 理由 / 待办 |
| --- | --- | --- | --- | --- |
| **1** | 整个 hook 都是**重写** `libcore.io.Linux.open/access` | AOSP 原实现 | ⚠️ **无法消除的偏差** | **已实测**：`libjavacore.so` 的 dynsym 里**没有** `Linux_open`/`Linux_access`（它们是 static 函数、经 `RegisterNatives` 注册）→ **拿不到原实现指针 → 无法透传 → 不存在零偏差方案** |
| **2** | 失败时抛 `ErrnoException` 而非 `FileNotFoundException` | v141 旧实现 | ✅ 已修正 | v148 对齐 AOSP（减少偏差），但**不追求"完全等价"** —— 因为前提（拿原实现）不成立 |
| **3** | `access` 失败抛异常而非返回 false | v141 旧实现 | ✅ 已修正 | 同上 |
| **4** | EINTR 重试 | v141 旧实现 | ✅ 已修正 | 同上 |
| **5** | 路径命中重定向规则时改写目标 | AOSP | ✅ **必要偏差** | 这是 hook 存在的唯一理由 |

## 以容器为中心的结论（重要）

> **容器的第一原则是不破坏 guest 可用性；能力缺失可以接受，破坏应用不可接受。**

| 层 | 偏差 | 覆盖面 | 评价 |
| --- | --- | --- | --- |
| `VirtualContext` 路径改写 | **0** | 所有"通过 Context 拿路径"的代码（~90% 场景） | ✅ 主力机制 |
| native GOT patch | 小 | 被 patch 的 so 里的 libc 调用 | ✅ 有效 |
| **本类（Java IO hook）** | **必然有**（重写 boot classpath 方法） | 仅"Java 硬编码路径"（`new File("/sdcard/x")`） | ❌ **收益 < 代价** |

**因此**：
1. **默认关闭**（`DebugFlags.isOn(JAVA_IO, false)`）；
2. **不再以"对齐 AOSP"为目标打磨它** —— 前提不成立，投入无回报；
3. 需要覆盖硬编码路径时，优先考虑 native 层（GOT patch）或其他注入点；
4. 真实应用回归（Via 可交互）**未通过前不得改默认值**。

## 约束与坑

1. **语义必须与 AOSP 等价**（v148 的教训）
   - `open` 失败 → 抛 `libcore.io.ErrnoException("open", errno)`
     （**不能**抛 `FileNotFoundException`：`catch (ErrnoException)` 的调用者会捕获不到）
   - `access` 失败 → **抛** `ErrnoException("access", errno)`
     （**不能**返回 `false`）
   - 两者都要 **EINTR 重试**
   - 反例：v141 只对齐成功路径 → Via 完全不可交互（见 `docs/输出/记录/记录-JavaIoHook导致Via不可交互.md`）

2. **默认关闭**（`DebugFlags.isOn(JAVA_IO, false)`）
   真实应用回归未通过前不要改默认值。

3. **改了开关必须重建 guest 进程**
   `LAUNCH` 对已运行进程只是复用，不会重新执行 `initNativeHooks`。
   验证方式：`UNINSTALL + INSTALL`，或让用户手动关闭后重开。

4. **安装时机**：必须在 native 库加载 + 规则同步之后
   （替换实现直接读 `g_path_redirects`），也就是 `NativeHookBridge.initNativeHooks`
   的最后一阶段。

5. **性能**：`FileDescriptor` 的构造器与 `ErrnoException` 构造器在 native 侧
   **首次解析后永久缓存**；不要退回"每次 FindClass"的写法。

6. **异常安全**：`ErrnoException` 拿不到时（@hide 类不可见）退回
   `FileNotFoundException`，并保证不残留 JNI 局部引用 —— hook 绝不能变成崩溃源。
