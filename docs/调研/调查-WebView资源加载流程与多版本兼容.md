# 调查：WebView 资源加载流程 + 多版本兼容的优雅方式

- 日期：2026-11-24
- 起因：用户要求 —— ①搜索正确优雅的多版本兼容方式（容器之后会面对版本复杂性，如 WebView 兼容）
  ②搜索 WebView 使用流程 ③向探针加 Activity，用实践证明正确方式和错误位置

---

## 一、WebView 的资源加载流程（AOSP 源码，决定性发现）

### 1.1 `WebViewFactory.getProviderClass()` 的核心十行

来源：[AOSP `core/java/android/webkit/WebViewFactory.java`](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android13-release/core/java/android/webkit/WebViewFactory.java)

```java
Context webViewContext = getWebViewContextAndSetProvider();
// ...（校验包名/版本/签名，然后）
for (String newAssetPath : webViewContext.getApplicationInfo().getAllApkPaths()) {
    initialApplication.getAssets().addAssetPathAsSharedLibrary(newAssetPath);
}
ClassLoader clazzLoader = webViewContext.getClassLoader();
WebViewLibraryLoader.loadNativeLibrary(clazzLoader, getWebViewLibrary(sPackageInfo.applicationInfo));
return getWebViewProviderClass(clazzLoader);
```

### 1.2 我上一轮的错误在哪（三个点全错）

| 维度 | **AOSP 的做法** | **我 `23b0538` 的做法** | 后果 |
| --- | --- | --- | --- |
| **API** | `addAssetPathAsSharedLibrary(path)` | `addAssetPath(path)` | **`addAssetPath` 会重建资源表** |
| **目标** | `initialApplication.getAssets()`（宿主/进程级） | `entry.assetManager`（guest Resources 专用的） | **污染 guest 的资源域** |
| **时机** | **`WebView` 第一次初始化时**（`getProvider()` 内部，AOSP 自己控制） | **`createResourcesForApp` 末尾**（Resources 已建好、已缓存） | **资源表已固化后再改 → cookie 失效** |

### 1.3 `addAssetPathAsSharedLibrary` 和 `addAssetPath` 的本质差别

**这正是 `0x02` 资源域的来源**：

| API | 资源域 | 说明 |
| --- | --- | --- |
| `addAssetPath(path)` | **`0x7f`**（应用域，和 guest 冲突） | 当作"同一应用的另一个 split" |
| **`addAssetPathAsSharedLibrary(path)`** | **`0x02` 起递增**（shared library 域） | 当作"共享库"，资源 ID 独立 |

**所以**：
- `WV.L5.onCreateActionMode` 读的 `0x20c0006` → **`0x02` 前缀 = shared library 域 = TrichromeLibrary 的资源**
- **它和 guest 的 `0x7f` 完全隔离**
- 我用 `addAssetPath` 强行塞进去 → **既是"错域"，又重建了资源表** → **双重错误**

### 1.4 推论：容器可能根本不需要做任何事

**如果 `initialApplication` 在 guest 进程里是容器**（`AppGlobals.getInitialApplication()` 返回 `NextVmApplication`），
**那么 AOSP 自己会**：
1. 调 `createApplicationContext(WebView 的 ApplicationInfo)` 建 WebView 的 context
2. 遍历 `getAllApkPaths()`（对 Trichrome = `WebViewGoogle64.apk` + `TrichromeLibrary64.apk`）
3. `initialApplication.getAssets().addAssetPathAsSharedLibrary(...)` → **加到容器的 AssetManager**
4. `WebView` 用的 `Resources` 就从这个 AssetManager 来

**那容器里的失败只可能在这几处**：

| # | 可能失败的点 | 怎么验证 |
| --- | --- | --- |
| **V1** | `createApplicationContext(ai, ...)` 在容器里返回错的东西（被 `VirtualContext`/hook 干扰） | 探针 W5 + 看 `BootCrumb` |
| **V2** | `getAllApkPaths()` 返回的路径在容器里不存在（沙箱重定向后） | **探针 W5** ← 已加 |
| **V3** | `initialApplication.getAssets()` 返回的是 **guest 的 AssetManager**（如果 `VirtualContext` 覆盖了 `getAssets()`） | **探针 W6** ← 已加 |
| **V4** | `addAssetPathAsSharedLibrary` 本身被容器的 native hook 影响 | 需要 native 侧日志 |
| **V5** | `waitForAndGetProvider()` 走的 `IWebViewUpdateService` 被 `SystemServiceProxyManager` 代理干扰 | 看 `SvcProxyMgr` 日志 |

**探针的 W5/W6/W7 就是为验证 V2/V3/V4 设计的。**

---

## 二、多版本兼容的优雅方式

### 2.1 AOSP 自己的范式（最好的范本）

**AOSP 面对"同一个任务在不同 API 上要用不同实现"时，用三层结构**：

```
① 按版本命名的实现类          WebViewChromiumFactoryProviderForT / ForU / ForV ...
② SDK_INT 静态分支            if (SDK_INT >= 30) useBuilder() else if (>= 28) useSetApkAssets() else useAddAssetPath()
③ 反射 + 能力探测             findMethod(clazz, "name", types) != null ? 用它 : 退到下一档
```

**关键洞察**：
> **AOSP 用的是"按版本命名的实现类 + 静态工厂"，不是"运行时 try-catch 兜底"。**
> **因为它需要"编译期可知"的行为契约**，而不是"跑起来再看"。

### 2.2 容器当前的兼容方式评估

`VirtualResourceManager.createAssetManagerForApp` 已经是 ② 结构：

```kotlin
val assetManager = when {
    Build.VERSION.SDK_INT >= 30 -> createAssetManagerViaBuilder(apkPath, splitApkPaths)
    Build.VERSION.SDK_INT >= 28 -> createAssetManagerViaApkAssets(apkPath, splitApkPaths)
    else -> createAssetManagerLegacy(apkPath, splitApkPaths)
}
```

**方向对，但缺一层：能力探测（③）**

**问题**：
- `SDK_INT` 只说明"Google 认为应该有这个 API"
- **厂商定制可能移除/改名**（本项目已遇到：`mAudioService` 字段在 Android 15 上不存在）
- **`SDK_INT` 分支写对了，但方法不存在** → 运行时 `NoSuchMethodException`

### 2.3 建议的优雅方式（三层 + 缓存）

```kotlin
// core/common/src/main/java/com/nextvm/core/common/ApiCompat.kt

/**
 * 能力探测：不看 SDK_INT，看"这个方法/字段到底在不在"。
 *
 * 为什么不能只看 SDK_INT：
 *   - 厂商定制可能移除/改名私有 API（本项目已踩：mAudioService 在 Android 15 上不存在）
 *   - 同一 SDK_INT 的不同 ROM 可能行为不同
 *   - 预览版 / GSI 的 API 可能和正式版不一致
 *
 * 为什么不能只 try-catch：
 *   - 失败一次之后每次都失败，但每次都要抛异常（性能 + 日志噪音）
 *   - 失败原因可能是"参数错"而不是"方法不存在"，会被误判成"不支持"
 */
object ApiCompat {

    private val methodCache = ConcurrentHashMap<String, Method?>()
    private val fieldCache = ConcurrentHashMap<String, Field?>()

    /** 能力探测（带缓存）。key 形如 "AssetManager#addAssetPathAsSharedLibrary(String)" */
    fun findMethodCached(clazz: Class<*>, name: String, vararg types: Class<*>): Method? {
        val key = "${clazz.name}#$name(${types.joinToString(",") { it.name }})"
        return methodCache.getOrPut(key) {
            runCatching {
                clazz.getMethod(name, *types).apply { isAccessible = true }
            }.getOrElse {
                runCatching {
                    clazz.getDeclaredMethod(name, *types).apply { isAccessible = true }
                }.getOrNull()
            }
        }
    }

    fun findFieldCached(clazz: Class<*>, vararg names: String): Field? {
        for (name in names) {
            val key = "${clazz.name}#$name"
            val f = fieldCache.getOrPut(key) {
                runCatching { clazz.getDeclaredField(name).apply { isAccessible = true } }.getOrNull()
            }
            if (f != null) return f
        }
        return null
    }

    /**
     * 按"能力"选择实现，而不是按 SDK_INT。
     *
     * 用法：
     *   val addSharedLib = ApiCompat.findMethodCached(AssetManager::class.java,
     *       "addAssetPathAsSharedLibrary", String::class.java)
     *   if (addSharedLib != null) { ... } else { 退到下一档 }
     */
}
```

**三个要点**：

| # | 要点 | 理由 |
| --- | --- | --- |
| **1** | **能力探测缓存** | 一次探测，永久复用；避免重复反射开销 |
| **2** | **多候选名**（`findFieldCached(clazz, "mAudioService", "mAudioServer", "sAudioService")`） | 厂商改名是常态 |
| **3** | **按能力选实现，不按 SDK_INT** | `SDK_INT=35` 但方法不存在时能退档 |

### 2.4 WebView 兼容的正确做法（基于 AOSP 源码）

**结论：容器不需要往 guest 的 Resources 里加任何 WebView 资源。**

**要做的只有三件事（按优先级）**：

| # | 事 | 说明 |
| --- | --- | --- |
| **1** | **不干扰 AOSP 的流程** | `WebViewFactory` 自己会 `addAssetPathAsSharedLibrary`。容器**只需保证 `VirtualContext.getAssets()` 返回容器的真实 AssetManager**（不能返回沙箱化的） |
| **2** | **保证 `getAllApkPaths()` 返回真实存在的路径** | Trichrome 的两个 APK 都在 `/product/app/`，不需要沙箱化 |
| **3** | **验证 `waitForAndGetProvider()` 能通** | 它走 `IWebViewUpdateService`，如果被 `SystemServiceProxyManager` 代理干扰，WebView 根本起不来 |

**换句话说**：
> **WebView 的资源问题是"容器干扰了 AOSP 的流程"，而不是"容器少做了某事"。**
> **修复方向是"少做"，不是"多做"。**

**这与我上一轮的做法完全相反。**

---

## 三、探针实验：`WebViewProbeActivity`（已加入，v1.9）

### 3.1 实验矩阵

| 编号 | 实验 | 判据 | 验证什么 |
| --- | --- | --- | --- |
| **W0** | `new WebView()` + `getSettings()` | 不抛异常 | **WebView 自己的 `0x02` 资源能否解析** |
| **W5** | `ApplicationInfo.getAllApkPaths()` | 返回的路径是否都存在 | **AOSP 加 shared library 用的路径对不对** |
| **W6** | `getAssets().open("webview_assets.json")` | 能打开 | **`addAssetPathAsSharedLibrary` 是否生效**（关键） |
| **W7** | `getResources().getInteger(0x20c0006)` | 能取到 | **直接验 popup 崩溃那个资源** |
| **W1** | `loadDataWithBaseURL("about:blank", html)` | `onPageFinished` + body 含 `WVPAGE_OK` | **渲染**（完全不碰文件） |
| **W2** | `loadUrl("file://" + getFilesDir() + "/wvprobe.html")` | 同上 | **file:// 读私有目录**（guest 视角 → 沙箱） |
| **W3** | `loadUrl("file:///android_asset/wvprobe.html")` | body 含 `ASSET_OK` | **assets（AssetManager 路径）** |
| **W4** | `loadUrl("file://" + getDataDir() + "/files/wvprobe.html")` | body 含 `WVPAGE_OK` | **dataDir 形式** |

### 3.2 判读表（这是关键）

| 结果组合 | 结论 |
| --- | --- |
| **W0 失败**（WebView 创建就抛异常） | **容器干扰了 AOSP 的 shared library 流程** → 查 W5/W6/V1-V5 |
| **W0 成功 + W6 MISS** | WebView 能用但**宿主 AssetManager 里没有它的资源** → 说明它走了另一条路（可能是 `WebViewContext` 而非 `initialApplication`） |
| **W0/W1 成功 + W2 失败** | **渲染正常，file:// 读私有目录失败** → **这才是"Via 主页"的问题域** |
| **W0/W1/W2 都成功 + W3 成功** | 容器的 WebView 完全正常 → **Via 的问题不在 WebView** |
| **W2 成功但 W4 失败** | `getFilesDir()` 和 `getDataDir()` 的沙箱映射不一致 → 查 `VirtualContext` |

### 3.3 触发方式

```powershell
# 先装探针到容器
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL \
    --es path /data/local/tmp/probe-v19.apk

# 启动实验页（不能用 am start，容器只解析 LAUNCHER）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH_ACTIVITY \
    --es pkg com.sandbox.probe \
    --es activity com.sandbox.probe.WebViewProbeActivity

# 读结果
adb logcat -d -s WVP
adb shell run-as com.nextvm.app sh -c 'find files/virtual/data -name wvprobe-result.txt'
```

### 3.4 为什么用探针而不是 Via 做实验

| 项 | 用 Via | **用探针 W0-W7** |
| --- | --- | --- |
| 变量数 | 多（Via 自己的逻辑、设置、URL 处理、Fragment 流程） | **每组只改一个** |
| 能否看到中间态 | 只能看最终界面 | **8 个独立判据** |
| 失败能否定位 | 只能看到 `InflateException` | **知道是"资源域"、"file 读取"还是"渲染"** |
| 可重复 | 差（依赖网络/缓存/多进程） | **好** |
| 会不会引入新回归 | **会**（上一轮就是这么把 Via 弄坏的） | **不会**（探针只是一个 Activity） |

---

## 四、对上一轮错误的完整复盘

| 环节 | 我的判断 | 实际 |
| --- | --- | --- |
| "`ensureWebViewResources` 零调用" | "忘了接线，接上就好" | **它是"事后 addAssetPath"，与 `Builder` 建的 AssetManager 不兼容** |
| "用 `addAssetPath` 加 Trichrome" | "加进去就有资源了" | **必须用 `addAssetPathAsSharedLibrary`（`0x02` 域）** |
| "加到 guest 的 `AssetManager`" | "guest 的 Resources 就能查到" | **应该加到 `initialApplication.getAssets()`（进程级）** |
| "在 `createResourcesForApp` 里接" | "这里刚好能拿到 instanceId" | **资源表已固化，改了会让 cookie 失效** |
| "Via 崩了是 Via 的问题" | — | **是我改的代码让它崩的** |

**最关键的一条**：
> **AOSP 里 `WebViewFactory` 已经完整处理了 shared library 的加载。**
> **容器该做的是"不干扰它"，而不是"替它做"。**

---

## 五、下一步

| 顺 | 动作 | 前提 |
| --- | --- | --- |
| **1** | **等 CI 完成，装回退版，验证 Via 恢复** | 时间切片测试 |
| **2** | **装探针 v1.9，跑 W0-W7** | 需要 probe CI |
| **3** | 按 W5/W6/W7 的结果，判断 V1-V5 的哪一个成立 | — |
| 4 | 引入 `ApiCompat`（能力探测 + 缓存 + 多候选名） | 独立，低风险 |
| 5 | 用 `ApiCompat` 重构 `createAssetManagerForApp` 的分支 | 依赖 4 |
| 6 | 修 `mAudioService` 等过时反射 | 用 4 |

**明确不做**：
- **不再往 guest 的 AssetManager 里加 WebView 资源**
- 在 W0-W7 出结果之前，**不碰 WebView 相关的任何代码**
