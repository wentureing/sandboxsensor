# 调研：Robolectric 适合为本项目测什么

- 日期：2026-11-24
- 结论：**只值得测 3 类**（Context 路径重写、Provider 初始化顺序、系统服务代理解析）；
  **WebView / native / 多进程一律不可能**，别在这上面花时间。
- 依据：[Android 官方 Robolectric 策略](https://developer.android.google.cn/training/testing/local-tests/robolectric) + [ShadowActivityThread API](https://robolectric.org/javadoc/4.12/org/robolectric/shadows/ShadowActivityThread.html) + 本项目代码盘点

---

## 一、Robolectric 的能力边界

| ✅ 能做 | ❌ 不能做 |
| --- | --- |
| Android framework 类的**行为**（`Context` / `Application` / `Activity` / `PackageManager` / `ContentProvider` / `Looper` / `Intent` / `Resources`） | **真实 native 代码**（JNI 走 stub，`System.loadLibrary` 后调不到真实现） |
| 用 Shadow 替换系统行为（如 `ShadowContextImpl` 注入假服务） | **真实 IPC / Binder 跨进程**（没有 system_server） |
| 资源加载（需 `isIncludeAndroidResources = true`） | **WebView 渲染 / JS 执行**（`ShadowWebView` 不加载真实页面、不跑 JS） |
| 多 SDK 版本模拟（`@Config(sdk = [...])`） | **真实权限 / SELinux / 文件系统 DAC** |
| `Application` / `ContentProvider` 生命周期 | **进程隔离**（单进程） |

**官方明确指出**：依赖"不支持的特性（如 WebView）"的代码不适合用 Robolectric 测。

**本项目已有基础**：`core:sandbox` 有两个 Robolectric 测试
（`RobolectricSmokeTest` 53 行、`HostReadPolicyTest` 195 行），
配置齐全（`RobolectricTestRunner` + `@Config(sdk = [35])` + `isIncludeAndroidResources = true`）。
`core:virtualization` 只加了依赖，未加 `testOptions`。

---

## 二、本项目可测点矩阵

| # | 目标 | Robolectric 可行性 | 价值 | 成本 | 建议 |
| --- | --- | --- | --- | --- | --- |
| **1** | **`VirtualContext` 的 26 个方法重写**（`getFilesDir` / `getDataDir` / `getExternalFilesDir` …） | ✅ **高**：纯路径计算 + `Context` 骨架 | **高** | **低** | **先做** |
| **2** | **`initContentProviders` 是否早于 `Application.onCreate`** | ✅ **中高**：Robolectric 支持 `ContentProvider` 生命周期 | **高**（AOSP 语义） | 中 | **次做** |
| **3** | `SystemServiceProxyManager` 是否装到 guest 拿到的服务实例 | ✅ **中**：`ShadowContextImpl` 可注入假服务 | 中 | 中 | 可做 |
| 4 | `VirtualApplicationManager.createApplication` Step 1→6 顺序 | ⚠️ **中低**：要 stub 出 `LoadedApk` / 反射字段 | 高 | **高** | 暂缓 |
| 5 | `ActivityThreadHook.handleMessage` 分支（100/113/114/159） | ⚠️ **低**：`ShadowActivityThread` **不提供 `mH` 消息队列**，要手工构造 `Message` + 假 `ActivityClientRecord` | 中 | **很高** | **不推荐** |
| 6 | `LegacyMessageHandlers` 三个 handler | ⚠️ **低**：全靠反射改隐藏类字段，Robolectric 里同样要造这些隐藏对象 | 中 | **很高** | **不推荐** |
| 7 | `PathRedirectRegistry` / `ProcSpoofing` / `TransactionItemDescriber` | — | — | — | **已用纯 JVM，不需要 Robolectric** |
| 8 | **WebView MIME 判定（W2 故障）** | ❌ **不可能** | — | — | **别试** |
| 9 | native GOT / 路径拦截 / `/proc` | ❌ **不可能** | — | — | **别试** |
| 10 | 多进程（`:p0` vs guest）、isolated 进程 | ❌ **不可能** | — | — | **别试** |

---

## 三、为什么第 1 项（`VirtualContext`）性价比最高

### 问题定位

`VirtualContext` 是**路径正确性的第一层**（L-Java，覆盖 ~90% 的路径请求）。
它有 **26 个方法重写**，每个都可能算错路径。

现在的验证方式：**真机 + 探针**（W34–W38）。问题是：
- 一轮要 4 分钟构建 + 装机 + 跑探针
- 只能覆盖探针写到的路径
- 出问题时不知道是**哪个方法**算错

### Robolectric 能补什么

```kotlin
@Test
fun `getFilesDir 在 detached 模式下返回合成路径`() {
    val base = ApplicationProvider.getApplicationContext<Context>()
    val vc = VirtualContext(base, guestPkg = "mark.via", instanceId = "1")
    assertTrue(vc.filesDir.absolutePath.contains("/virtual/data/mark.via_1"))
}

@Test
fun `getDataDir 与 getFilesDir 的父目录一致`() { ... }
```

**收益**：26 个方法的路径计算可以在**毫秒级**验证，且失败时直接指出是哪个方法。
**成本**：`VirtualContext` 依赖 `VirtualContext.super`（容器 ContextImpl）+ 若干管理器，
需要构造最小依赖 —— 这是唯一的工作量。

---

## 四、第 2 项（Provider 顺序）为什么值得

### 为什么重要

`VirtualApplicationManager.createApplication` 的 **Step 5 早于 Step 6** 是 AOSP 语义
（见 [`架构-hook关键路径.md`](../../架构/架构-hook关键路径.md) 路径 C2）。

**顺序反了的现象**：Provider 里拿不到初始化好的 Application 单例 →
用 `androidx.startup` / Firebase 的应用偶发 NPE 或初始化丢失。
这种 bug 在真机上**很难复现和归因**。

### Robolectric 能验什么

```kotlin
@Test
fun `ContentProvider onCreate 早于 Application onCreate`() {
    val order = mutableListOf<String>()
    // Provider 里记录 "provider"，Application.onCreate 里记录 "app"
    assertEquals(listOf("provider", "app"), order)
}
```

**成本**：需要 stub `initContentProviders` 依赖（`VirtualContext` / `ProviderInfo`）。
比第 1 项高，但比第 4/5/6 项低。

---

## 五、明确不建议的方向（附原因）

| 方向 | 为什么不做 |
| --- | --- |
| **用 Robolectric 查 WebView MIME** | `ShadowWebView` 不加载真实资源、不跑 JS、不实现 `AndroidProtocolHandler`。W2 是真实 WebView 行为，Robolectric 帮不上 |
| **测 `ActivityThread.mH` 消息分发** | `ShadowActivityThread` 的 API 只有 `currentActivityThread/currentApplication/getPackageManager`，**没有 `mH` 队列模拟**。要手工构造 `Message` + 反射造 `ActivityClientRecord`，测试会极其脆弱（android 版本一变就崩） |
| **测 `LegacyMessageHandlers`** | 同上，且这些 handler 的输入是 framework 内部对象（`ActivityClientRecord`），Robolectric 不提供构造器 |
| **测 native hook** | Robolectric 的 native 是 stub |
| **测多进程行为** | 单进程 |
| **替代真机回归** | 不能。Robolectric 验证的是"逻辑分支"，真机验证的是"进程级初始化顺序 / 类加载 / native 规则注册" |

---

## 六、建议的实施顺序

| 顺 | 内容 | 前置 | 预期收益 |
| --- | --- | --- | --- |
| **1** | `core:virtualization` 加 `testOptions { unitTests { isIncludeAndroidResources = true } }` | — | 让 Robolectric 可用 |
| **2** | `VirtualContextTest`：26 个方法的路径矩阵（正常 / detached / 外部存储 / null 边界） | 1 | **路径正确性毫秒级验证** |
| **3** | 如果第 2 项顺利，再加 `initContentProviders` 顺序测试 | 1、2 | 钉住 AOSP 语义 |
| 4 | （可选）`SystemServiceProxyManager` 代理解析 | 1 | 服务是否装对 |

**第 2 项是唯一"高价值低成本"的**，其余都该等真机基线稳定后再评估。

---

## 七、与真机回归的关系

| 层 | 能证明什么 | 不能证明什么 |
| --- | --- | --- |
| 纯 JVM 单测 + golden | 纯函数逻辑 | 任何 Android 交互 |
| **Robolectric** | **Android API 交互的正确分支**（Context 路径、Provider 顺序） | native、IPC、真实 WebView、进程级顺序 |
| 真机 + 探针 | 端到端真实行为 | 难以定位到具体方法 |

**三者互补，Robolectric 填的是"Android API 交互"这一格** —— 现在这一格是空的
（`core:virtualization` 一个 Robolectric 测试都没有）。
