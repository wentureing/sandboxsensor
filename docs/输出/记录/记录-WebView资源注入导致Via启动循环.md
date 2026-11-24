# 记录：WebView 资源注入导致 Via 启动循环（严重回归）

- 日期：2026-11-24 18:15
- 版本：容器 `196fc44`（含 `23b0538` 的 WebView 接线）
- 结果：**Via 完全无法启动，每 0.5 秒崩溃一次（boot loop）**
- 处置：**已回退（`2fc764d`）**

---

## 一、现象

用户报告"现在启动 via 连 activity 都没了"。用**时间切片**（先记录手机时间 T0，再 `adb logcat -T T0`）抓到：

```
18:15:53.919 am_proc_start: [0,26206,...,com.nextvm.app:p1,...]
18:15:54.401 am_proc_died:  [0,26206,com.nextvm.app:p1,0,2]
18:15:54.405 am_proc_start: [0,26305,...]
18:15:54.888 am_proc_died:  [0,26305,...]
18:15:54.893 am_proc_start: [0,26467,...]
...
```

**每 0.5–1 秒换一个 pid，循环不止。**

**BootCrumb 每次都走到同一步就断**：

```
[+120ms] [main] cb onActivityStarted mark.via.Shell     ← 最后一条
（然后进程死）
```

**从来没有 `onActivityResumed`**。

---

## 二、真因

```
18:15:54.373 E TransactionExecutor: Failed to execute the transaction: tId:-458144077
18:15:54.374 E AndroidRuntime: FATAL EXCEPTION: main
18:15:54.374 E AndroidRuntime: Process: mark.via, PID: 26206
18:15:54.374 E AndroidRuntime: android.view.InflateException:
    Binary XML file line #48 in mark.via:layout/p:
    Error inflating class android.widget.TextView
18:15:54.374 E AndroidRuntime: Caused by: java.lang.reflect.InvocationTargetException
18:15:54.374 E AndroidRuntime: Caused by: java.lang.UnsupportedOperationException:
    Failed to resolve attribute at index 5: TypedValue{t=0x2/d=0x7f040027 a=-1}
      at android.content.res.TypedArray.getColorStateList(TypedArray.java:608)
      at android.widget.TextView.readTextAppearance(TextView.java:4887)
      at android.widget.TextView.<init>(TextView.java:1760)
```

### 关键：`a=-1` 是 `TypedValue.assetCookie`

- `d=0x7f040027` = **Via 自己的资源 ID**（`0x7f` 是应用资源域）
- `a=-1` = **assetCookie 无效** → AssetManager 里**找不到这个资源 ID 对应的 APK**

**也就是说：Via 自己的资源在它自己的 Resources 里解析失败了。**

### 为什么

我（`23b0538`）在 `createResourcesForApp` 里这样接的线：

```kotlin
// ResourceImpl 已经建好、Resources 已经建好、已写入 resourceCache
ensureWebViewResources(instanceId)   // ← 事后 addAssetPath 145MB 的 TrichromeLibrary64.apk
```

而 `ensureWebViewResources` 用的是**旧 API**：

```kotlin
val addAssetPath = findMethod(AssetManager::class.java, "addAssetPath", arrayOf(String::class.java))
addAssetPath.invoke(entry.assetManager, path)
```

**但 API 30+ 的 AssetManager 是这么建的**：

```kotlin
// createAssetManagerViaBuilder
val builder = builderCtor.newInstance()
addApkAssetsMethod.invoke(builder, sysApkAssets)      // 1. system framework
addApkAssetsMethod.invoke(builder, guestApkAssets)    // 2. guest APK
for (splitPath in splitApkPaths) { ... }              // 3. splits
val assetManager = buildMethod.invoke(builder)        // 4. build() ← 资源表在此固化
```

**混用两种 API 的后果**：
1. `build()` 时资源表已固化，`ResourcesImpl` 缓存了各 APK 的 cookie
2. 事后 `addAssetPath` **重建资源表**
3. 已创建的 `ResourcesImpl` 仍持**旧 cookie** → `a=-1`

**而且**：WebView 的资源域和 guest 一样都是 `0x7f`，直接 `addAssetPath` 会造成**资源 ID 冲突**。
Trichrome 本质是 **shared library**，应该按 shared-library 方式加载。

---

## 三、这个错误的性质

**这是我第 7 次同类错误，而且是最严重的一次**：

| # | 错误 | 后果 |
| --- | --- | --- |
| 1 | `N03`/`N06` 失败 → 判"重定向生效" | 结论错 |
| 2 | 搜不到调用 → 判"死代码" | 结论错 |
| 3 | 只打日志 → 判"该删" | 差点删掉能力 |
| 4 | `Log.wtf` → 判"必然失败" | 结论错 |
| 5 | 函数名含 "Via" → 判"Via 专属" | 结论错 |
| 6 | 查到"平台限制" → 判"不修" | **主动关闭调查** |
| **7** | **看到"函数实现了但零调用" → 直接接线** | **把可用功能改坏（Via 完全不能启动）** |

**第 7 次的特殊性**：

> **前面 6 次都是"结论错"，这次是"动手错"。**
> **我看到 `ensureWebViewResources` 零调用，就认定"接线就好了" —— 完全没检查：**
> - **它的实现和当前 AssetManager 的创建方式是否兼容**
> - **API 30+ 还能不能事后 `addAssetPath`**
> - **Trichrome 是 shared library，能不能直接加**
>
> **"零调用"不等于"忘了调"，也可能是"作者试过发现不行所以没接"。**

**这正好印证了项目 `AGENTS.md` 里那句**：
> 不要轻易判定死代码 / 不要从局部信号下结论

**我上一轮才写进文档的教训，这一轮就违背了。**

---

## 四、正确的实现方向（还没做）

**必须做法**：

```kotlin
// createAssetManagerViaBuilder 内部
// 3. Add split APKs
for (splitPath in splitApkPaths) { ... }

// 3.5 【新增】Add WebView ApkAssets —— 在 build() 之前
for (path in WEBVIEW_APK_PATHS) {
    if (!File(path).exists()) continue
    val wvAssets = loadApkAssets(path, isSystem = true)   // ← shared library 语义
    if (wvAssets != null) addApkAssetsMethod.invoke(builder, wvAssets)
}

// 4. Build
val assetManager = buildMethod.invoke(builder) as AssetManager
```

**关键点**：

| # | 要点 |
| --- | --- |
| 1 | **必须在 `build()` / `setApkAssets()` 之前加** |
| 2 | **API 28-29 分支同理**（`setApkAssets` 之前加入 list） |
| 3 | **API < 28 legacy 分支**：在最后一次 `addAssetPath` 后不要再动 |
| 4 | **`isSystem` 参数**要确认（shared library 可能需要 `true`） |
| 5 | **资源域冲突**：WebView 的 `0x7f` 和 guest 的 `0x7f` 会冲突吗？需要验证 |

---

## 五、待验证的问题（正确实现前必须答）

| # | 问题 | 怎么查 |
| --- | --- | --- |
| 1 | `loadApkAssets(path, isSystem=true)` 和 `false` 的实际差别 | 读 `loadApkAssets` 实现 |
| 2 | Trichrome 作为 shared library 加载的正确 API | AOSP `ApkAssets.loadFromPath` 的 flags |
| 3 | 两个 `0x7f` 资源域的 APK 能否共存于一个 AssetManager | 小实验：探针里试 |
| 4 | **Via 到底需不需要 WebView 资源在自己的 Resources 里** | **可能根本不需要** —— WebView 的 `Resources` 应该从系统 WebView 的 context 拿，不是 guest 的 |

**第 4 点最关键**：
> **`WV.L5.onCreateActionMode` 读 `Resources.getInteger(0x20c0006)` —— 这个 `Resources` 是哪个？**
> **是 WebView 自己的 `Resources`（来自 WebView 的 context），还是 guest 的？**
>
> **如果 WebView 用的是自己的 Resources，那容器根本不需要往 guest 的 AssetManager 里加任何东西。**
> **`0x20c0006` 前缀是 `0x02`（不是 `0x7f`）→ 说明那是 `WebViewGoogle64.apk` 的资源（package id 0x02）。**

**这才是正确的方向：查清 WebView 从哪拿 Resources，而不是盲目注入。**

---

## 六、当前状态

| 项 | 状态 |
| --- | --- |
| 回退 | ✅ `2fc764d` |
| CI | ✅ 已触发 |
| `WEBVIEW_APK_PATHS` 的 11 条路径 | ✅ 保留（Trichrome + 64 后缀，v156 兼容） |
| `ensureWebViewResources` 函数 | ✅ 保留（作为参考，零调用） |
| **WebView popup 崩溃** | ❌ **暂不修**（比"Via 完全不能用"轻得多） |
| **正确实现** | ⏳ 待查清"WebView 从哪拿 Resources" |

---

## 七、教训（写给自己）

**"零调用"的三种可能，我这次只想到第一种**：

| # | 可能 | 该怎么区分 |
| --- | --- | --- |
| 1 | 作者忘了接 | 看实现是否与当前架构兼容 |
| 2 | **作者试过发现不行，所以没接** | **看实现是否与当前架构兼容** ← 我漏了 |
| 3 | 作者打算以后接 | 看 TODO 注释 |

**判断一个"零调用"的功能能不能直接接线，必须先回答**：

1. **它的实现用的是哪个版本的 API？** （`ensureWebViewResources` 用 legacy `addAssetPath`，而创建用 `Builder`）
2. **它的时机对吗？** （资源表已固化后不能再改）
3. **它的前提成立吗？** （Trichrome 是 shared library，不是普通 APK）
4. **有没有更简单的方案？** （WebView 可能根本不需要 guest 的资源）

**这次如果先做第 4 点的调查，就不会动手。**
