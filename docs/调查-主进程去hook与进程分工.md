# 影响范围调查：主进程不装 hook + 测试逻辑移到 hook 进程

- 日期：2026-11-24
- 起因：用户明确 ——
  ① 不应给主进程装 hook
  ② 但主进程也需要一定的地址转换能力
  ③ 可能需要分配给另一个进程
  ④ WebView 的代理调用是否需要分配给另一个进程
  ⑤ CmdReceiver 接受命令后启动测试，**希望测试本身运行在 hook 的进程**
  ⑥ 继续调查影响范围
- 结论先给：**主进程不装 hook 的阻力比预想小得多**（`AntiDetectionEngine` 整套是死代码），
  **但"测试移到 hook 进程"需要一个新的进程内命令通道**

---

## 一、主进程不装 hook：影响范围逐一核查

### 1.1 主进程里所有"可能需要地址转换"的地方

| # | 位置 | 做什么 | 需要转换吗 | 不装 hook 后 |
| --- | --- | --- | --- | --- |
| 1 | `installApp` → `File(apkPath)` + `parseFullPackage` | 读真实 APK | ❌ **不需要**（读的是真实路径） | ✅ **变好**（不再被重定向） |
| 2 | `copyApkToVirtualStorage` → `File.copyTo` | 读真实 APK + 写沙箱 | ❌ 不需要（两边都是真实路径） | ✅ **变好** |
| 3 | `discoverSplitApks` | 读 APK 目录 | ❌ 不需要 | ✅ 变好 |
| 4 | `SandboxManager.collectProbeOutput` | 读沙箱文件（真实路径） | ❌ 不需要 | ✅ 变好 |
| 5 | `SandboxManager.listProbeOutput` | 同上 | ❌ 不需要 | ✅ 变好 |
| 6 | `uninstallApp` | 删沙箱文件（真实路径） | ❌ 不需要 | ✅ 变好 |
| 7 | `AntiDetectionEngine.initialize()` | **只打日志**（见 §1.2） | — | ✅ 无影响 |
| 8 | `AntiDetectionEngine.hookRootChecks()` 等 | 19 处 `hidePath` | — | ⚠️ **本来就是死代码**（见 §1.2） |

**关键结论**：
> **主进程的所有文件访问用的都是"真实路径"**（`/sdcard/...` 是真实的，沙箱路径也是真实的），
> **所以主进程根本不需要"地址转换"，它需要的是"不要被转换"。**
> **不装 hook 正好满足这个需求。**

### 1.2 关键发现：`AntiDetectionEngine` 整套是**死代码**

```kotlin
// AntiDetectionEngine.kt:140
fun initialize() {
    if (initialized) return
    initialized = true
    Timber.tag(TAG).i("AntiDetectionEngine initialized — anti-detection ready")   // ← 只打日志
}

// AntiDetectionEngine.kt:161 —— 真正干活的方法
fun enableAntiDetection(instanceId: String, level: DetectionLevel = DetectionLevel.MODERATE) {
    instanceLevels[instanceId] = level
    hookRootChecks()          // ← 19 处 hidePath 都从这里来
    hookEmulatorChecks()
    if (level >= DetectionLevel.MODERATE) { hookVirtualEnvChecks(); hookXposedChecks() }
    if (level >= DetectionLevel.AGGRESSIVE) { hookStackTraceCleanup() }
}
```

**全仓库搜索 `enableAntiDetection`**：**只有定义，零调用**。

**所以**：
- **19 处 `hidePath` 从未执行过**
- **容器现在完全没有"隐藏自己"的能力**
- **`P16`（容器 dataDir 可 stat）失败不是 bug，是功能压根没启用**
- **`AntiDetectionEngine` 的 400+ 行是死代码**

**这对 P0-3 的意义**：
> **主进程不装 hook 不会破坏 anti-detection，因为它本来就没在跑。**
> **P0-3 的风险从"中"降到"低"。**

### 1.3 已存在的进程判断能力（可复用）

```kotlin
// PackageManagerProxy.kt —— 已在用 7 次
callerResolver.isGuestProcess()
```

**所以"区分 guest 调用与容器调用"的能力已经有了** —— P0-4（容器包不可见）可以直接用。

---

## 二、用户第 ⑤ 点：测试逻辑移到 hook 进程

### 2.1 现状回顾

```
CmdReceiver.onReceive（主进程主线程）
  → engine.installApp(path)          ← 主进程，装 hook
  → sandbox.collectProbeOutput()     ← 主进程，装 hook
```

**问题**：
- 这些测试**测的是"容器主进程 + hook"的行为**，不是 guest 的行为
- 用户希望"**测试本身运行在 hook 的进程**"

### 2.2 三种可行的改造方式

#### 方式 A：`CmdReceiver` 转发到 guest 进程的 stub Service

```kotlin
// 主进程 CmdReceiver（不装 hook）
ACTION_TEST -> {
    val intent = Intent().apply {
        setClassName(context.packageName, "com.nextvm.app.stub.P0\$S00")   // :p0 进程
        putExtra("test", testName)
        putExtra("args", args)
    }
    context.startService(intent)      // 跨进程
}
```

**问题**：
- stub Service（`P0$S00`）是**容器自己的类**，在 `:p0` 进程 → **它跑的是容器的代码，但进程名已改成 guest 包名**
- `:p0` 进程里的 ClassLoader 可能已被替换成 guest 的 → **调用容器的类会失败**
- **需要验证**：`:p0` 进程能否执行容器自己的代码

#### 方式 B：用 `ParcelFileDescriptor` 把结果传回来

```
主进程 CmdReceiver（不装 hook）
  → 打开一个 pipe（真实路径，不需要转换）
    → 把 pipe 的 fd 传给 guest 进程
      → guest 进程里跑测试，把结果写 pipe
        → 主进程读出结果，打 logcat
```

**优点**：结果通道不依赖路径转换
**缺点**：复杂

#### 方式 C：**让测试跑在"专门的测试进程"**

新增一个 `:probe` 进程（manifest 加 `android:process=":probe"`），里面：
- **装 hook**（因为要模拟 guest 环境）
- **但不加载 guest 的 ClassLoader**（跑容器自己的测试代码）
- 用**真实路径**读 APK（因为它有自己的 hook 配置，可以不重定向 `/sdcard/Download`）

**优点**：
- **进程职责清晰**：主进程 = 控制 + 真实路径；`:probe` 进程 = 带 hook 的测试
- `CmdReceiver` 转发到 `:probe` 只是 `startService`，简单
- **不依赖 guest 的 ClassLoader**

**缺点**：新增一个进程（内存 + 启动开销）

### 2.3 三种方式对比

| 项 | A（转发到 `:p0`） | B（pipe 传结果） | **C（新增 `:probe` 进程）** |
| --- | --- | --- | --- |
| 改动量 | 小 | 中 | 中（manifest + Service + 转发） |
| 能否跑容器代码 | ⚠️ 存疑（ClassLoader 可能已换） | ✅ | ✅ |
| 能否模拟 guest 环境 | ✅（就是 guest 进程） | ✅ | ⚠️ 需要自己配 hook |
| 结果通道 | Binder | pipe | **logcat（最简单，与现在一致）** |
| 风险 | 中 | 中 | **低** |
| **推荐** | | | **✅ C** |

---

## 三、用户第 ④ 点：WebView 代读放哪个进程

### 3.1 现状

`WebResourceResponse` / `shouldInterceptRequest` **零命中** → **P1-3 还没开始**。

### 3.2 代读的本质需求

```
WebView 的 renderer（isolated 进程）请求 file:///sdcard/x.html
  → 容器需要：读到 /sdcard/x.html 的真实内容
    → 包成 WebResourceResponse 返回
```

**关键**：**代读代码运行在哪个进程？**

| 位置 | 能读到真实路径吗 | 能注入 WebViewClient 吗 |
| --- | --- | --- |
| **主进程**（不装 hook） | ✅ **能** | ❌ WebView 在 guest 进程里 |
| **guest 进程**（装 hook） | ❌ 会被重定向 | ✅ 能 |

**矛盾**：
- **代读要在主进程**（能读真实路径）
- **注入要在 guest 进程**（WebViewClient 在那里）

### 3.3 解法：**guest 进程请求，主进程代读**

```
guest 进程（装 hook）
  WebViewClient.shouldInterceptRequest(url)
    → 通过 Binder/AIDL 调主进程的服务
      → 主进程（不装 hook）读真实文件
        → 通过 Pipe/PFD 把 fd 传回
          → guest 进程用 fd 构造 InputStream
            → WebResourceResponse(InputStream)
```

**关键**：**`ParcelFileDescriptor` 可以跨进程传递** —— 这是唯一能把"主进程读到的内容"交给 guest 的方式。

**所以**：
> **WebView 代读确实需要"分给另一个进程"** —— 具体是**主进程（或任何不装 hook 的进程）做读取，guest 进程做注入**。

### 3.4 这与其他需要"真实读取"的功能是同一个模式

| 功能 | 谁读真实路径 | 谁用结果 |
| --- | --- | --- |
| `installApp` | 主进程 | 主进程（写入沙箱） |
| `collectProbeOutput` | 主进程 | 主进程 |
| **WebView 代读** | **主进程** | **guest 进程（通过 PFD）** |
| **测试（用户第 ⑤ 点）** | — | `:probe` 进程 |

**所以架构上应该有一个统一的"真实路径读取服务"**：

```
主进程（不装 hook）
  └─ RealPathService（AIDL）
       ├─ openRealFile(path) -> ParcelFileDescriptor
       ├─ statRealPath(path) -> struct stat
       └─ listRealDir(path) -> String[]
```

**所有需要真实读取的组件（guest 进程、`:probe` 进程）都通过它拿 fd。**

**这是"地址转换能力分配给另一个进程"的正确形态**。

---

## 四、综合架构建议

### 4.1 三个进程角色

| 进程 | 装 hook | 职责 |
| --- | --- | --- |
| **主进程** `com.nextvm.app` | ❌ **不装** | ①容器 UI/管理 ②**真实路径读取服务**（`RealPathService`）③`CmdReceiver` 只做转发 |
| **guest 进程** `:p0`–`:p9` | ✅ 装 | 跑 guest；需要真实文件时通过 `RealPathService` 拿 fd |
| **测试进程** `:probe`（新增） | ✅ 装 | 跑"带 hook 的测试"（用户第 ⑤ 点） |

### 4.2 数据流

```
[装 APK]
adb → CmdReceiver（主进程，无 hook）
        → installApp 直接读真实 /sdcard/Download/x.apk ✅
          → 写到沙箱（真实路径）✅

[跑探针]
adb → CmdReceiver（主进程）
        → startService(:probe)              ← 转发
          → :probe 进程（装 hook）跑测试
            → 结果走 logcat + 文件

[WebView 代读]
guest 进程（装 hook）的 WebViewClient
  → AIDL → 主进程 RealPathService
    → 主进程读真实文件（无 hook）
      → 返回 ParcelFileDescriptor
        → guest 进程构造 WebResourceResponse

[CmdReceiver 的其它命令]
LIST / UNINSTALL / COLLECT_OUT → 主进程直接做（都是真实路径操作）✅
```

### 4.3 影响范围清单

| 改动 | 影响 | 风险 |
| --- | --- | --- |
| **主进程不装 hook** | ①`installApp` 不再需要豁免 ②可删 `is_import_read_exempt` ③`AntiDetectionEngine` 无影响（死代码） | **低**（比预想低） |
| **新增 `:probe` 进程** | manifest +1 个 Service + `CmdReceiver` 转发 | 低 |
| **`RealPathService`（AIDL）** | 新组件；guest 进程要改调用方式 | 中（要改 guest 侧的读文件逻辑） |
| **WebView 代读** | 依赖 `RealPathService` | 中高（P1-3 本身） |

---

## 五、还需要验证的三件事

| # | 项目 | 怎么验 |
| --- | --- | --- |
| 1 | `:p0` 进程能否执行容器自己的代码（方式 A 是否可行） | 加日志看 stub Service 能否被拉起 |
| 2 | 主进程不装 hook 后，`COLLECT_OUT`/`LIST` 等是否仍正常 | 实测 |
| 3 | `ParcelFileDescriptor` 跨进程传 fd 后，guest 能否正常读 | 用探针加一项 |

---

## 六、结论

| 用户的问题 | 答案 |
| --- | --- |
| **不应给主进程装 hook** | ✅ **对**，而且**阻力比预想小** —— `AntiDetectionEngine` 19 处 `hidePath` 是死代码（`enableAntiDetection` 零调用） |
| **主进程也需要地址转换能力** | ❌ **不需要** —— 主进程所有访问用的都是真实路径，它需要的是"不被转换" |
| **可能分配给另一个进程** | ✅ **需求真实存在，但方向相反** —— 不是"把转换能力给别的进程"，而是"**把读取能力留在主进程**，让 guest 通过 fd 来取" |
| **WebView 代读是否分给另一个进程** | ✅ **需要** —— guest 进程注入、主进程读取，用 `ParcelFileDescriptor` 连 |
| **测试希望运行在 hook 的进程** | ✅ **推荐新增 `:probe` 进程**（不依赖 guest 的 ClassLoader，职责清晰） |
| **影响范围** | 见 §4.3：主进程不装 hook 风险低；新增进程和 AIDL 是主要工作量 |
