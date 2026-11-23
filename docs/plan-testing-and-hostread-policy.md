# NEXTVM 测试策略 + 宿主代读权限边界（计划）

- 日期：2026-11-23
- 来源：用户明确的两条计划
- 状态：**设计阶段，未编码**

---

## 零、你的两条计划（原文转述 + 结构化）

### 计划 A：测试要"用容器自己的调试能力"，而不是"guest 是黑盒只能看 logcat"

- 着重**利用和强化本身的调试能力**
- **测试与普通代码完整分割**
- 允许使用**低影响的测试框架**
- **在 debug 包开启完整测试**

### 计划 B：宿主代读的权限边界

> 每个 app **只允许读取自己的区域**，以及 **NEXTVM 被授权读取的外部区域**。

---

## 一、现状盘点：容器已有的调试设施（比想象中强）

| 设施 | 位置 | 能力 | 现状 |
| --- | --- | --- | --- |
| **`GuestBreadcrumb`** | `core:virtualization/lifecycle` | **199 处埋点**；`begin/attachFile/log/logThrowable/filesDir`；4000 行环形缓冲；写 `breadcrumb-<pid>.log` 含完整栈 | ✅ 已有，**但只在故障时被读** |
| **`StateProbe`** | 同上 | 反射采样 AMS/Disposable 等内部状态；`threadNamesNoSuspend()`；按 key 采样 | ✅ 已有 |
| **`DiagSuspendGate`** | 同上 | 暂停门（避免诊断本身干扰 guest） | ✅ 已有 |
| **`WebViewWatch`** | 同上 | 40ms 采样主线程栈，记录 WebView 初始化 | ✅ 已有 |
| **`WebViewWarmup`** | 同上 | 反射预取 provider + 修 `LoadedApk.dataDir` | ✅ 已有 |
| `BuildConfig.DEBUG` | 12 处 | 已有条件编译习惯（如 `NextVmApplication.kt:27`） | ✅ 已有 |
| **`src/debug` / `src/androidTest` / `src/test`** | — | — | ❌ **完全空白** |

**结论**：诊断**采集**能力很强，但**没有测试**、也没有"把诊断变成可断言结果"的桥梁。

---

## 二、计划 A 落地方案：三层分割

```
第 1 层  诊断钩子（main 源集，永远在，但默认空实现）
         core/virtualization/src/main/.../diag/DiagnosticsSink.kt
         ↓ 只有一个 interface + 一个 no-op 单例
第 2 层  诊断实现（独立模块，仅 debug 依赖）
         :core:diagnostics  （debugImplementation）
         ↓ 实现 Sink：把 Breadcrumb/StateProbe/WebViewWatch 的数据
           变成「可断言的快照 + 可查询的结构」
第 3 层  测试（独立源集，不进包）
         src/test（JVM/Robolectric） + src/androidTest（设备）
```

### 2.1 第 1 层：只有一个钩子接口（生产代码零负担）

`core/virtualization/src/main/.../diag/DiagnosticsSink.kt`：

```kotlin
/** 诊断出口。release 构建里永远是 NoOp，不产生任何开销。 */
interface DiagnosticsSink {
    fun onEvent(phase: String, detail: String)
    fun onSnapshot(name: String, value: String)
}

object Diagnostics {
    @Volatile
    var sink: DiagnosticsSink = NoOp

    fun event(phase: String, detail: String) = sink.onEvent(phase, detail)
    fun snapshot(name: String, value: String) = sink.onSnapshot(name, value)

    private object NoOp : DiagnosticsSink {
        override fun onEvent(phase: String, detail: String) {}
        override fun onSnapshot(name: String, value: String) {}
    }
}
```

**关键点**：
- `main` 源集里只有接口 + NoOp，**零依赖、零开销**
- 现有 199 处 `GuestBreadcrumb.log()` **不需要改**——由第 2 层在 debug 下把两者桥接

### 2.2 第 2 层：独立 `:core:diagnostics` 模块

```
core/diagnostics/
  build.gradle.kts          → api(project(":core:virtualization"))
  src/main/.../DiagInstaller.kt   → 把 Diagnostics.sink 换成真实实现
  src/main/.../BreadcrumbTap.kt   → 镜像 GuestBreadcrumb 的输出
  src/main/.../SelfCheckRunner.kt → 启动时自动跑一组自检
```

`app/build.gradle.kts`：

```kotlin
dependencies {
    debugImplementation(project(":core:diagnostics"))   // release 完全不包含
}
```

`app/src/debug/.../DebugInit.kt`（debug 源集）：

```kotlin
object DebugInit {
    fun install(app: Application) {
        if (!BuildConfig.DEBUG) return
        DiagInstaller.install(app)        // 来自 :core:diagnostics
        SelfCheckRunner.schedule(app)     // debug 包自动跑完整自检
    }
}
```

**为什么用独立模块而不是 `src/debug` 源集**：

| 对比 | `src/debug` 源集 | **独立模块 + debugImplementation** |
| --- | --- | --- |
| release 是否包含 | 不含 | 不含 |
| 能否独立演进/复用 | 受限于模块划分 | **可以独立成库** |
| 依赖注入点 | 需要在 main 里留 `if (DEBUG) Class.forName(...)` | 编译期就有类型 |
| 测试能否直接引用 | 需要 `testDebug` 变体 | 直接依赖 |

**代价**：多一个模块（约 50 行 Gradle + 200 行代码）。

### 2.3 第 3 层：测试（框架选择，按"低影响"排序）

| 框架 | 影响 | 能测什么 | 建议 |
| --- | --- | --- | --- |
| **JUnit4 + Robolectric** | **极低**（纯 JVM，无设备） | 路径解析、`SandboxManager`、`VirtualClassLoader`、`PackageManagerService` 的纯逻辑 | ✅ **主力** |
| **JUnit4 + MockK** | 极低 | 依赖注入后的单元测试 | ✅ 配合 Robolectric |
| **androidTest + Espresso** | 低（独立 APK，不污染主包） | UI 流程 | ⚠️ 只在必要时 |
| **UIAutomator** | 零（不碰生产代码） | 跨应用流程（如 Gaia 测试） | ✅ 已在用（`gaia-test.ps1`） |
| **自研 SelfCheck** | 低（debug-only） | 容器内环境自检（如"能否建沙箱""能否拉 guest"） | ✅ **这是"用容器自己的调试能力"的关键** |

**最关键的取舍**：**不要为测试去改生产代码结构**。当前 `VirtualEngine` 14k 行、31 个函数，如果为了可测性去抽接口，会引入大量回归风险。

**所以**：
- **测"纯函数"**（路径解析、MIME 推断、APK 解析）→ Robolectric，零侵入
- **测"集成行为"** → `SelfCheckRunner`（debug 包内），它调用真实引擎并断言结果
- **测"跨进程/UI"** → UIAutomator（已有 `gaia-test.ps1`）

### 2.4 "在 debug 包开启完整测试"的具体形态

```kotlin
// core/diagnostics/SelfCheckRunner.kt
object SelfCheckRunner {
    /** debug 包启动 5 秒后自动跑，结果写 /sdcard/Android/data/<pkg>/files/selfcheck.json */
    fun schedule(app: Application) {
        if (!BuildConfig.DEBUG) return
        Handler(Looper.getMainLooper()).postDelayed({
            val results = listOf(
                Check("sandbox.create", ::checkSandboxCreate),
                Check("sandbox.pathShape", ::checkPathShape),
                Check("hook.installed", ::checkHookInstalled),
                Check("guest.fork", ::checkGuestFork),
                Check("webview.dataDir", ::checkWebViewDataDir),
                Check("hostread.policy", ::checkHostReadPolicy),
            ).map { it.run() }
            write(results)
        }, 5_000)
    }
}
```

**为什么这是"用容器自己的调试能力"**：它直接调用 `SandboxManager` / `NativeHookBridge` / `WebViewWarmup` 的真实 API，断言"容器自己认为它做对了什么"，而不是从外部猜。

---

## 三、计划 B：宿主代读的权限边界

### 3.1 允许清单（默认拒绝）

```
✅ guest 自己的沙箱
   /data/user/0/<容器>/app_virtual/data/<instanceId>/**       （实例数据）
   /data/user/0/<容器>/app_virtual/apks/<instanceId>.apk      （实例 APK）

✅ NEXTVM 被授权读取的外部区域
   /sdcard/Android/data/<容器>/**                             （容器自己的外部目录）
   /storage/emulated/0/Android/data/<容器>/**
   /sdcard/Download/**                                        （已有 p18 导入豁免）
   /storage/emulated/0/Download/**

❌ 其他一律拒绝
   /data/data/<其他应用>
   /data/user/0/<其他应用>
   /system/**  /vendor/**  /apex/**
   其他 guest 的沙箱（跨实例）
```

### 3.2 实现要点（四条硬约束）

```kotlin
object HostReadPolicy {

    fun resolve(instanceId: String, requested: String): String? {
        // ① 必须做 realpath 规范化，防 ../ 逃逸
        val real = try { File(requested).canonicalPath } catch (t: Throwable) { return null }

        // ② 自己的沙箱（严格前缀 + 分隔符，防前缀攻击）
        val own = File(sandboxDataDir(instanceId)).canonicalPath + File.separator
        if (real.startsWith(own)) return real

        // ③ 容器外部区域
        for (allowed in HOST_ALLOWED) {
            val p = allowed.canonicalPath + File.separator
            if (real.startsWith(p)) return real
        }

        // ④ 其他一律 null（= 不代读）
        return null
    }
}
```

**四条必须做到的**：

| # | 约束 | 为什么 |
| --- | --- | --- |
| **①** | **`canonicalPath` 规范化** | 防 `../../../data/data/<其他应用>` 逃逸 |
| **②** | **前缀后必须加 `File.separator`** | 防 `/x/data/abc` 匹配到 `/x/data/abcd` |
| **③** | **按 instanceId 隔离** | A guest 不能读 B guest 的沙箱 |
| **④** | **符号链接不跟随外层** | 沙箱内若有指向外部的软链，`canonicalPath` 会解析出去 → 正好被 ②③ 拦住 |

### 3.3 与现有能力的关系

| 已有 | 复用方式 |
| --- | --- |
| `SandboxManager` 的路径构造 | `HostReadPolicy` 直接复用它算沙箱根，不自己拼字符串 |
| p18 的 `/sdcard/Download` 导入豁免 | 白名单与它保持一致（同一份常量） |
| `NativeHookBridge` 的 `redirect_path` | 宿主代读的路径**不走** native 重定向（它已经是真实路径） |

### 3.4 一个必须想清楚的边界情况

**guest 请求 `/sdcard/Download/x.html`**：

- 这个路径**本身就是允许的**（p18 已豁免读）
- 但它是**容器的外部目录**还是**用户的真实下载目录**？
  - 在容器里，`/sdcard` 被重定向进沙箱 → guest 看到的 `/sdcard/Download` 是 `<沙箱>/sdcard/Download`
  - **宿主代读时，应该代读哪一个？**

**建议**：
- **优先沙箱内的 `/sdcard/Download`**（与 guest 的视角一致）
- 如果沙箱内没有，**再考虑**真实 `/sdcard/Download`（需显式开关）

这决定了"guest 看到的文件系统"与"宿主代读的文件系统"是否一致——**不一致会导致"能列目录但打不开文件"这类诡异问题**。

---

## 四、落地顺序（建议）

```
阶段 1（0.5 天）  加 DiagnosticsSink 接口 + NoOp（main 源集，零风险）
阶段 2（1 天）    建 :core:diagnostics 模块 + DebugInit（debug 源集接入）
阶段 3（1 天）    SelfCheckRunner 的 6 个检查项 + 结果落盘
阶段 4（1 天）    HostReadPolicy + 单测（Robolectric，纯函数好测）
阶段 5（按需）    Espresso / androidTest（只在改 UI 时加）
```

**为什么这个顺序**：
- 阶段 1 是纯增量（不改变任何现有行为）
- 阶段 2-3 让"容器自己报告它做了什么"成为可能，**这才是"不把 guest 当黑盒"的实质**
- 阶段 4 是纯函数，最容易写测试且有真实价值（安全边界）

---

## 五、一句话总结

> **测试能力的短板不是"采集"，而是"断言"**：`GuestBreadcrumb`/`StateProbe` 已经能采集大量过程数据，但没有任何机制把"容器认为做对了什么"变成可自动判定的结果。
> **所以优先级最高的是 `SelfCheckRunner`**——它让容器在 debug 包里自己回答"沙箱建好了吗 / hook 装上了吗 / guest 起来了吗 / 代读策略对吗"，而不是靠外部 logcat 反推。
