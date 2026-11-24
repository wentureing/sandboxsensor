# 深度调查：测试器的 hook 状态、R0–R3 取舍、理想容器的边界

- 日期：2026-11-24
- 起因：用户要求 ①检查容器自己的测试器（不是 cmd 本身，而是 cmd 调用的测试）是否在独立线程、是否被 hook
  ②不执行"减少痕迹"（后续还要开发）③深入调查 R0–R3 的范围取舍 ④理想容器应该做到什么程度

---

# 第一部分：测试器的线程与 hook 状态（已查清）

## 一、`CmdReceiver` 与它调用的测试逻辑

### 1.1 线程模型：**全在主进程主线程，全程同步**

```kotlin
/ CmdReceiver.kt:58
override fun onReceive(context: Context, intent: Intent) {
    val engine = EntryPointAccessors.fromApplication(...).engine()      / 主线程
    when (action) {
        ACTION_INSTALL -> {
            val r = engine.installApp(path)                             / ← 阻塞，主线程
            Log.i(TAG, "INSTALL $path -> $r")
        }
        ACTION_COLLECT_OUT -> {
            val collected = sandbox.collectProbeOutput()                / ← 阻塞，主线程
        }
        ...
    }
}
```

| 检查项 | 结果 |
| --- | --- |
| 是否指定 `android:process` | ❌ 没有 → **主进程** |
| 是否 `goAsync()` | ❌ 没有 |
| 是否新线程 / 协程 / Executor | ❌ 没有 |
| **结论** | **全部在主进程的主线程（`ActivityThread.main` 的 Looper）** |

### 1.2 主进程确实装了 hook（实测，vivo A15）

```
10:18:14.872  8729 29702 I NextVM-Native: Installing PLT hooks via GOT patching...
10:18:14.885  8729 29702 I NextVM-Native: PLT hooks installed successfully
10:18:14.886  8729 29702 I NativeHook: Native PLT hooks initialized: true
```

`8729` 就是**容器主进程**的 pid。

**同一时刻系统库被跳过**（符合 `NS_HOOK_SYSTEM_LIBS=0`）：
```
GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0): /apex/com.android.art/lib64/libartpalette.so
GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0): /apex/com.android.art/lib64/libdexfile.so
...
```

### 1.3 测试逻辑的路径访问点

```kotlin
/ VirtualEngine.kt:345 installApp
fun installApp(apkPath: String): VmResult<VirtualApp> {
    val apkFile = File(apkPath)                              / ← File 构造不触发 IO
    val result = frameworkApkParser.parseFullPackage(apkFile) / ← 这里读文件（open）
    ...
    File(splitPath).copyTo(destFile, overwrite = true)       / ← 这里也读文件
```

**`parseFullPackage(File)` 内部必然走 `FileInputStream` / `RandomAccessFile` → `open()` → native hook。**

## 二、完整因果链（这是 `is_import_read_exempt` 存在的根本原因）

```
1. 容器主进程启动 → VirtualEngine.initialize() → initNativeHooks()
     → 主进程的 app 库 GOT 被 patch（实测日志确认）

2. adb 发广播 → CmdReceiver.onReceive（主进程主线程）
     → engine.installApp("/sdcard/Download/x.apk")
       → FileInputStream("/sdcard/Download/x.apk")
         → open()  ← 【被 hooked_open 拦截】
           → redirect_path("/sdcard/Download/x.apk")
             → 匹配 /sdcard 规则 → 改到 <沙箱>/sdcard/Download/x.apk
               → 沙箱里没这个文件 → ENOENT
                 → 报 "APK file not found"

3. 所以 p18 加了 is_import_read_exempt：
     open() / openat() 对 /sdcard/Download、/data/local/tmp 前缀"放行"
       → 不改路径 → 读到真实文件 → 装包成功
```

**关键结论**：
> **`is_import_read_exempt` 不是在修"guest 读不到文件"，而是在修"容器自己读不到文件"。**
> **它的存在本身就证明了：主进程不该装 hook。**

**这也回答了 P0-3 的收益**：主进程不装 hook → `installApp` 直接读真实路径 → **整个豁免机制可以删掉**（连带删掉它引入的两个越权分支）。

## 三、这个发现改变了对"测试器"的看法

| 之前以为 | 实际 |
| --- | --- |
| `CmdReceiver` 是"外部控制通道"，与容器内部隔离 | **它在主进程主线程，和容器共享同一套 hook** |
| 测试器的 `open` 应该直通 | **被重定向了**，所以才需要豁免 |
| `run-as` 能测应用权限（已纠正过） | **`CmdReceiver` 也被 hook，所以它的行为不代表"真实应用行为"** |

**推论**：
> **任何通过 `CmdReceiver` 做的"应用行为测试"，测的都是"容器主进程 + hook"的行为，不是 guest 的行为。**
> 要测 guest 的真实行为，必须在 guest 进程里跑（探针就是这么做的）。

---

# 第二部分：R0–R3 的深入取舍

## 四、六个维度完整对比

| 维度 | **R0**（现有 GOT patch） | **R1**（patch `libnativeloader`） | **R2**（inline hook `libc`） | **R3**（shadowhook 只 hook `dlopen`） |
| --- | --- | --- | --- | --- |
| **1. 覆盖能力** | 已被 patch 的库 | 所有 `System.loadLibrary` 加载的库 | **所有调用者**（含 `dlsym` 后调用、库内部调用） | 所有 so 加载（只加回调） |
| **2. 隐蔽性代价** | **低**：每 patch 一个库，该库的 GOT 改动 1–N 项 | **中**：多 patch 一个系统库（`libnativeloader`） | **高**：`libc` 的 `.text` 被改（校验和、指令扫描都能发现） | **低**：只改 `android_dlopen_ext` 一个函数 |
| **3. 开发成本** | 已完成 | **~20 行**（加入 patch 列表 + 验证库已加载） | **3–4 天**（自研 inline hook 引擎 + 按调用方过滤） | **2 天**（引入依赖 + 只在回调里调现有 patch） |
| **4. 维护成本** | 低 | 低（依赖 `libnativeloader` 的实现细节） | **高**（每个 Android 版本的 `libc` 指令序列不同） | 低（shadowhook 自己适配） |
| **5. 失败模式** | 该库不 patch → 路径不重定向（**症状轻**） | 库加载链断 → **guest 起不来** | **整个进程崩**（含 ART） | 回调不执行 → 退回 R0 行为（**症状轻**） |
| **6. 与"guest 难以发现"的一致性** | **一致性高**：痕迹少 | 一致性中：多一个系统库被 patch | **一致性低**：改 `libc` 是最容易被发现的 | **一致性高** |

## 五、关键判断：R2 与"难以发现"目标**相冲突**

这是一个反直觉的结论，但很重要：

| 目标 | R2 的效果 |
| --- | --- |
| **"拦住所有调用"** | ✅ R2 最强（连 `dlsym` 后调用都能拦） |
| **"guest 难以发现"** | ❌ **R2 最差** —— 因为： |

1. **`libc.so` 的 `.text` 被改** → guest 可以：
   - 读 `/proc/self/maps` 确认 `libc` 的映射是 `r-xp`（正常）但**内容变了**
   - **计算 `.text` 段的校验和**（很多加固壳会做）→ **直接发现**
   - **扫描函数序言**（前 4–16 字节）→ inline hook 必然改这里

2. **`libc` 是所有库都依赖的** → 改它影响面最大 → **异常行为最容易暴露**

3. **`mprotect` 调用本身** → 可以在 `seccomp` 或 `audit` 层被观测（虽然应用层看不到）

**所以**：
> **R2 是"拦截能力最强、隐蔽性最差"的方案。**
> **它适合"根本不在乎被发现"的场景（如纯功能增强），不适合"要骗过加固壳"的场景。**

**要骗过加固壳的正确姿势**：
- **不在 `.text` 留痕迹**（R0/R1/R3 都不改 `.text`）
- **GOT 改动要"看起来合理"**（改成一个"像是真 libc 地址"的值？做不到 —— GOT 值必须是真实函数地址）
- **或者：让 guest 看不到 GOT**（这里没有好办法）

**结论**：**加固壳检测和 hook 技术是"矛与盾"，没有"既全拦又不被发现"的方案。**

## 六、组合方案（不是单选）

实际部署应该是**分层组合**，而不是"选一条"：

| 场景 | 组合 |
| --- | --- |
| **普通应用**（不检测虚拟化） | **R0 足够**（现状） |
| **轻度检测**（看 `getDataDir`、`maps`） | R0 + L1（Java 代理）+ L4（拒绝访问） |
| **中度检测**（看 `maps` 找容器库） | R0 + L3（maps 伪造） |
| **重度加固**（`dlsym` 后调用、`.text` 校验） | **只能放弃"难以发现"，转 R2 硬拦** |

**所以选型的核心问题是**：**你的 guest 分布是什么？**

| 目标 guest 类型 | 建议路线 |
| --- | --- |
| 普通 App（社交/工具） | R0 + L1 + L4（**当前计划**） |
| 有风控的 App（电商/金融） | + L3（maps 伪造）+ R1（补 L6） |
| **加固壳保护的 App**（银行/游戏） | **R0–R3 都不够**，需要承认"这条路走不通"或转 R2 硬碰 |

## 七、R1 和 R3 的实际差别（这个之前没写清）

两者都能解决 L6，但机制不同：

| 项 | **R1**（patch `libnativeloader` 的 GOT） | **R3**（inline hook `android_dlopen_ext`） |
| --- | --- | --- |
| 拦截点 | `libnativeloader.so` 的 GOT 槽 | `android_dlopen_ext` 的函数体 |
| 前提 | `libnativeloader` 在 `nativeInit` 时**已加载** | 无前提 |
| 失效场景 | `libnativeloader` 之后被重新加载（`dlclose`+`dlopen`）→ GOT 重置 → 链断 | 无（改的是函数体） |
| 痕迹 | `libnativeloader` 的 GOT 有 1 项被改 | `android_dlopen_ext` 前 16 字节被改 |
| 被发现难度 | 需要"检查系统库 GOT"（**很少应用做**） | 需要"检查 `libdl` 的函数序言"（**加固壳会做**） |
| 崩的影响 | 库加载失败 → guest 起不来 | 只影响加载，不影响其他 |

**判断**：
- **R1 的隐蔽性反而比 R3 好**（检查系统库 GOT 的应用极少）
- **R3 的稳定性更好**（不依赖加载顺序）
- **两者都不完美**，但都远好于 R2

**所以推荐的组合是**：**R1（首选，隐蔽性好）+ 若 R1 因加载时机失败，退到 R3**。

## 八、一个被忽略的选项：**R4 —— 不 hook，改配置**

| 思路 | 做法 | 可行性 |
| --- | --- | --- |
| **让 guest 的路径本来就是真的** | 把 guest 的 `dataDir` 设成 `/data/user/0/<容器>/gaia/data/user/0/<pkg>`（Gaia 的做法） | ✅ 可行（但路径含容器包名） |
| **或者：用 `bind mount`** | 需要 root | ❌ |
| **或者：让 guest 用 `ContentResolver`** | 只覆盖走 ContentResolver 的应用 | ⚠️ 部分 |

**"路径保真"的代价**：路径里含容器包名 → 被 `getDataDir()` 一看就知道。

**但**：**如果 Java 层伪造了 `getDataDir()`，native 层又不用改**（因为路径本来就是真的）→ **只需要一层伪造，不需要 hook**。

**这可能是一条比 R1/R2/R3 都更"干净"的路**：

| 项 | R1/R3（重定向） | **R4（路径保真）** |
| --- | --- | --- |
| 需要 hook | 需要 | **不需要**（或只需极少量） |
| 痕迹 | GOT / 函数体改动 | **零** |
| 代价 | — | 路径含容器包名（Java 层可伪造） |
| 风险 | 中 | **低** |
| Android 版本依赖 | 有 | **无** |

**为什么 Gaia 走这条路**：`/data/user/0/com.app.hider.../gaia/data/user/0/com.sandbox.probe` —— **路径是真的**，不需要重定向。

**我们的问题**：我们的沙箱是 `files/virtual/data/<pkg>_<ts>`，**也是真路径**！
- **那为什么还需要重定向？**
- **因为 guest 认为自己的 dataDir 是 `/data/data/<pkg>`**
- **而真实路径是 `/data/user/0/<容器>/files/virtual/data/<pkg>_<ts>`**

**所以**：**如果让 guest 直接用真实路径（不翻译），就不需要重定向** —— 但**路径会暴露容器**。

**这就是"兼容性 vs 隐蔽性"的根本取舍**（见 `架构/架构-拦截能力层次.md` §3.5）。

---

# 第三部分：理想容器应该做到什么程度

## 九、先定义"发现"的层级

| 层级 | guest 的检测手段 | 被发现后果 |
| --- | --- | --- |
| **D1 被动感知** | 无（只是路径/返回值不同） | 无 |
| **D2 应用层自查** | `getDataDir()`、`getPackageInfo`、`checkSelfPermission` | 上报、限制功能 |
| **D3 文件系统检查** | `stat`/`access`/`opendir` 找容器 | 同上 |
| **D4 进程检查** | `/proc/self/maps`、`cmdline`、`status` | 同上 |
| **D5 GOT/`.text` 检查** | 扫描自身库的重定位表、函数序言 | **强证据** |
| **D6 行为异常** | `stat` 与 `readdir` 不一致、耗时异常 | 强证据 |

## 十、理想状态：分三档定义

### 档 1：**"普通应用不会察觉"**（最低要求，可达成）

| 检测层 | 目标 |
| --- | --- |
| D1 | ✅ 天然满足 |
| **D2** | ✅ **全部造假**（Java 层代理） |
| D3 | ✅ `stat`/`access`/`opendir` 一致地"容器不存在" |
| D4 | ⚠️ **部分**（`cmdline` 可伪造，`maps` 难） |
| D5/D6 | ❌ 不做（普通应用不检查） |

**实现**：`P0-4` + `P0-5` + 现有 L1。

### 档 2：**"有风控的应用也难过"**（中期目标）

| 检测层 | 目标 |
| --- | --- |
| D4 | ✅ `maps` 伪造（去掉容器路径/库名） |
| D5 | ⚠️ GOT 改动不可避免，但可**减少数量**（删无谓 hook） |
| D6 | ✅ 假代理成组一致 |

**实现**：档 1 + `L3`（maps 伪造）+ `R1`（补 L6）。

### 档 3：**"骗过加固壳"**（**不可达，要明确放弃**）

| 检测层 | 为什么不可达 |
| --- | --- |
| D5（`.text` 校验） | **只要 hook 就必须改函数体/GOT** → 校验必然失败 |
| D6（耗时） | hook 必然有开销 |
| **反调试** | `ptrace` 自检、`TracerPid` 检查 |
| **Play Integrity / 硬件证明** | **容器签名必然不是原应用** → 服务器一验就知道 |

**必须明确写下来的边界**：
> **凡是"服务器端验证应用签名/完整性"的检测，容器在原理上就不可能通过。**
> （Play Integrity API、SafetyNet、自建签名校验服务）
> **这不是"做得不够好"，是"这条路本身走不通"。**

## 十一、理想容器的"可接受不一致"清单

**即使做到最好，以下不一致必须被接受**：

| # | 不一致 | 为什么不可避免 | 影响 |
| --- | --- | --- | --- |
| 1 | **签名不是原应用** | guest 用的是原 APK 但签名被容器的签名替代（或有多个签名） | 服务器验签必失败 |
| 2 | **uid 是容器的** | fork 不换 uid | `Process.myUid()` 与真机不同 |
| 3 | **`/proc/self/maps` 里有容器库** | 容器必须加载自己的 so | D4 检测 |
| 4 | **GOT 有改动** | hook 的必然结果 | D5 检测 |
| 5 | **`getppid()` 是容器主进程**（或 zygote） | 进程由容器启动 | D4 检测 |
| 6 | **安装来源（`installerPackageName`）** | guest 不是通过系统 PackageInstaller 装的 | D2 检测（可伪造） |
| 7 | **WebView renderer 在 isolated 进程** | Android 强制 | WebView 本地文件不可用 |
| 8 | **`/data/data/<pkg>` 不存在** | 真实数据在沙箱 | D3 检测（可伪造为"存在且可访问"，但内容不同） |

## 十二、理想容器的"成熟度分级"

| 级别 | 标准 | 现在的位置 |
| --- | --- | --- |
| **M0 能跑** | guest 能启动、能显示界面 | ✅ 已达到 |
| **M1 能装** | APK 能导入、能启动、能卸载 | ✅ 已达到 |
| **M2 能自检** | 有探针能量化不一致 | ✅ 已达到（74 项） |
| **M3 普通应用无感** | 档 1 全部达成 | ⚠️ 差 `P13`/`P16` |
| **M4 有风控的应用难察觉** | 档 2 达成 | ❌ 差 maps 伪造 + L6 |
| **M5 加固壳** | 档 3 | ❌ **不可达** |

**当前在 M2 和 M3 之间。**

## 十三、所以"理想容器"的准确定义

> **理想容器 = 让 guest 在"应用层能看到的一切"都与真机一致，
> 同时接受"系统层必然不一致"（uid / 签名 / maps / GOT）。**

**具体到可执行的目标**：

| 目标 | 可达成 | 手段 |
| --- | --- | --- |
| `getDataDir()` 返回一个"看起来正常"的路径 | ✅ | Java 层伪造 |
| `getPackageInfo(容器)` 抛 `NameNotFoundException` | ✅ | Java 层过滤 |
| `stat(容器路径)` 返回 `ENOENT` | ✅ | `nativeHidePath` + `readdir` |
| `cmdline` / `status` 显示 guest 包名 | ✅ | `/proc/self` spoof |
| `maps` 里不出现容器路径 | ⚠️ 难但 Gaia 做到了 | memfd 伪造 |
| `maps` 里不出现容器库 | ❌ **做不到**（库必须加载） | — |
| **GOT 无改动** | ❌ 做不到（hook 的必然结果） | — |
| **`.text` 无改动** | ✅ 可做到（**只要不用 R2**） | 用 R0/R1/R3 |
| **签名与真机一致** | ❌ 做不到 | — |

**这就是"理想"的真实边界**：
- **Java 层 + 路径层：可以做到"完全一致"**
- **进程层（maps/cmdline）：可以做到"看起来一致"**
- **二进制层（GOT/.text）：只能"减少痕迹"，不能消除**
- **签名层：完全不可能**

## 十四、由此得出的技术选型建议（重新排序）

按"理想容器"的定义，选型应该**优先保证"不会留下二进制痕迹"**：

| 优先 | 选型 | 理由 |
| --- | --- | --- |
| **1** | **R0 + L1 + L4**（现状 + Java 代理 + 拒绝访问） | 零额外二进制痕迹，覆盖 D2/D3 |
| **2** | **L3 maps 伪造** | 覆盖 D4，只在 `open("/proc/self/maps")` 时介入 |
| **3** | **R1**（patch `libnativeloader`） | 补 L6，只改系统库 GOT 1 项（**极少应用检查**） |
| **4** | R3（shadowhook） | R1 失效时的备选 |
| **❌ 不推荐** | **R2（inline hook `libc`）** | **改 `.text` → D5 必然被发现 → 与"理想容器"目标直接冲突** |

**关于"减少无谓痕迹"（用户说不执行，但记录）**：
- 那不是"优化"，是**D5 检测面的直接减少**
- 建议在 R1 落地时**一起做**（反正要改 `native-hook.cpp` 的 hook 表）
