# 记录：Gaia 内跑同一套 W 实验 —— 结果正常，推翻「沙箱路径必然嗅探」

- 时间：2026-11-24 20:00（device）
- 设备：`10AFA8169Z0039G`（vivo V2463A / Android 15 / API 35 / arm64-v8a）
- 容器：**应用隐藏大师（Gaia）** `com.app.hider.master.pro.cn`
- 探针：**v1.13**（`probe-33b8e4b-arm64-v8a.apk`，从 `/sdcard/Download/probe-v113-33b8e4b.apk` 由用户手动导入 Gaia）
- 运行方式：**在 Gaia 里点图标**（v1.13 起 WebViewProbeActivity 是 LAUNCHER）
- guest 进程：pid `21134`，前台 `com.prism.gaia.client.stub.GuestActivityStub$Guest1`
- 取结果：`adb logcat -d -s WVP`（Gaia 沙箱 adb 读不到，见 §五）

---

## 一、最重要的三条

1. **Gaia 里 `W2` 完全正常**：`ct=text/html`，`body` 是渲染后的 `WVPAGE_OK hello from probe`，**不是源码**。
2. **Gaia 里 `W3`（`file:///android_asset/`）也正常**：`ASSET_OK loaded from android_asset`。
   而我们容器里 W3 是 `EMPTY` + `Unable to open asset URL`。
3. **Gaia 也没有做任何 WebView hook** —— 它是「路径镜像」路线（`gaia/data/user/0/<pkg>`），
   路径里同样含容器包名、同样在内部存储、同样是 app 私有目录。

**→ 「沙箱前缀的 file:// 必然走内容嗅探」这个结论是错的。**
它只对**我们容器**成立。我之前基于 W17′（`GIF89a` → `image/gif`）推出的机制是真实观察，
但把它当成「沙箱路径的普遍行为」是过度概括。

---

## 二、逐项对照（同一探针、同一台设备、同一 WebView 版本 126.0.6478.71）

| 实验 | 内容 | **我们容器（nextvm）** | **Gaia** |
| --- | --- | --- | --- |
| W1 | `loadDataWithBaseURL` | `text/html` 渲染 ✅ | `text/html` 渲染 ✅ |
| **W2** | `file://<沙箱>/wvprobe.html` | **`text/plain` 源码** ❌ | **`text/html` 渲染** ✅ |
| **W3** | `file:///android_asset/wvprobe.html` | **`EMPTY`** ❌（`Unable to open asset URL`） | **`ASSET_OK` 渲染** ✅ |
| W4 | 沙箱另一个 `.html` | `text/plain` ❌ | `text/html` ✅ |
| W14 | `file:///data/local/tmp/...` | `text/html` ✅ | `text/html` ✅ |
| W15 | 沙箱 + `?x=.html` | `text/plain` ❌ | `text/html` ✅ |
| W16 | 沙箱 `.txt`（内容是 HTML） | `text/plain` | `text/plain`（**按扩展名**，正常） |
| W17 | 沙箱 + adb 放入的文件 | `text/plain`（文件在） | `exists=false`（**Gaia 沙箱放不进去，无效**） |
| W18 | `/data/user/0/com.nextvm.app/files/...` | `text/plain` | `ERR_FILE_NOT_FOUND`（写不进去，无效） |
| W19 | `file:///sdcard/...` | `EMPTY`（写时 EPERM） | `ERR_FILE_NOT_FOUND`（写时 EPERM，无效） |
| **W30** | 反射包装 `WebViewClient` | **未跑成**（那版没接线） | **`FAIL: WebView 里找不到 WebViewClient 类型字段`** |

**W16 是关键旁证**：Gaia 里 `.txt` 得到 `text/plain`、`.html` 得到 `text/html` ——
**Gaia 是按扩展名判 MIME 的**（正常 WebView 行为）。我们容器里 `.html` 和 `.txt` 都是 `text/plain`，
因为都走「嗅探 → HTML 无魔数 → 默认 text/plain」。

---

## 三、新结论：根因在「WebView 看到的 Application / Assets 视图」

Gaia 正常 + 我们异常，而两边路径形态几乎一样（都含容器包名、都在 `data/user/0` 下），
所以差异不在路径字符串，而在**WebView 内部拿到的 Context**。

两条异常是**同一个根因**，而且都指向 Java 侧 `AndroidProtocolHandler`：

```java
// chromium android_webview/java/src/.../AndroidProtocolHandler.java
ContextUtils.getApplicationContext().getAssets()      // ← asset 走这里
ContextUtils.getApplicationContext().getContentResolver()
URLConnection.guessContentTypeFromStream(stream)      // ← 普通 file 的兜底
```

- **W3 打不开 asset**：`openAsset()` 用 `ContextUtils.getApplicationContext().getAssets().open(path)`。
  如果这个 context 是**容器**的（`com.nextvm.app`），它当然找不到 guest 的 `assets/wvprobe.html`
  → `Unable to open asset URL`。
- **W2 变 text/plain**：普通 `file://` 走到 `guessContentTypeFromStream`（只认 GIF/JPEG/PNG/序列化魔数）
  → HTML 没有魔数 → null → 上层默认 `text/plain`。

**推断**：我们容器里 WebView 的 `ContextUtils.getApplicationContext()` 指向的是**容器的 Application**，
而不是 guest 的。Gaia 显然让 WebView 看到了 guest 的 Application，所以 asset 和 MIME 都正常。

> 注意：W8 的 `SAME_CONTEXT=true` 不能反驳这一点 —— 那是 **`WebView` 这个 View 的 Context**
> （等于 Activity），而 WebView 内部用的是**进程级静态** `ContextUtils.sApplicationContext`。

---

## 四、对修复方案的影响（重要）

| 原方案 | 现在的判断 |
| --- | --- |
| **方案 A：hook `shouldInterceptRequest` 补 MIME** | **W30 在 Gaia 上直接失败**：`WebView` 类里按类型扫 `declaredFields` **找不到** `WebViewClient` 字段（Android 15 的 client 存在 provider 里）。这条路要么改走 `getWebViewProvider()`，要么放弃 |
| **方案 B：改路径前缀** | 不必了 —— Gaia 证明**同一路径形态可以不改任何东西就正常**，根因不在路径 |
| **新方案 C：修 WebView 侧看到 Application** | 这才是对着根因的修法，且能**一次修好 W2 + W3**（Via 主页 + Via 的 logo.svg） |

**方案 C 的候选落点**（待验证）：

1. `org.chromium.base.ContextUtils.sApplicationContext`（WebView APK dex 里的静态字段）
   —— 通过 WebView provider 的 ClassLoader 反射设为 guest 的 Application
2. 或者容器在 guest 进程里对 `ActivityThread.currentApplication()` 的返回做修正
   （WebView 初始化时用它填 `ContextUtils.sApplicationContext`）

**下一步实验（v1.14，W31）**：在**容器**里读出 WebView 侧真实的 Application 身份：

```java
WebView wv = ...;
Object provider = WebView.class.getMethod("getWebViewProvider").invoke(wv);
ClassLoader cl = provider.getClass().getClassLoader();
Class<?> cu = Class.forName("org.chromium.base.ContextUtils", false, cl);
Context c = (Context) cu.getMethod("getApplicationContext").invoke(null);
rec("W31 ContextUtils.getApplicationContext().packageName = " + c.getPackageName());
```

判据：

| W31 结果（容器内） | 含义 |
| --- | --- |
| 包名 = `com.nextvm.app` | **根因确认**：WebView 看到的是容器 Application → 做方案 C |
| 包名 = `com.sandbox.probe` | 推断错 → 回到「asset/MIME 的另一条分支」重新找 |

---

## 五、Gaia 的结果文件（为什么只能靠 logcat）

v1.13 试了 3 个输出路径，**Gaia 里全部写入成功**：

```
OUT  [filesDir]      /data/user/0/com.app.hider.master.pro.cn/gaia/data/user/0/com.sandbox.probe/files/wvprobe-result.txt  OK
OUT  [externalRoot]  /storage/emulated/0/Android/data/com.app.hider.master.pro.cn/gaia/com.sandbox.probe/wvprobe-result.txt  OK
OUT  [externalFiles] /storage/emulated/0/Android/data/com.app.hider.master.pro.cn/gaia/com.sandbox.probe/Android/data/com.sandbox.probe/files/wvprobe-result.txt  OK
```

但**没有一个 adb 拿得到**（实测）：

| 通道 | 结果 |
| --- | --- |
| `ls /data/user/0/com.app.hider.master.pro.cn/` | `Permission denied` |
| `run-as com.app.hider.master.pro.cn` | `package not debuggable` |
| 设备 root | 无 `su` |
| `/storage/emulated/0/Android/data/<gaia>/gaia/...` | `Permission denied`（历史记录已证） |

→ **`adb logcat -d -s WVP` 是唯一出口**。v1.13 已加「跑完用 `R| ` 前缀把完整报告重放一遍」，
一次 dump 就能拿全：

```powershell
adb logcat -d -s WVP | Select-String "R\|" | ForEach-Object { ($_.Line -split 'R\| ',2)[1] }
```

---

## 六、附带发现

1. **`/sdcard` 字面量在 Gaia 里写会 EPERM**，但 `Environment.getExternalStorageDirectory()`
   会被 Gaia 重定向到它自己的镜像（`.../gaia/com.sandbox.probe/...`）→ 能写。
   所以我们容器文档里「Gaia 不能写外部存储」的说法要修正：**它写的是自己的镜像，不是真外部存储**。
2. **W30 的失败本身是有价值的**：说明「反射扫 `WebView.declaredFields` 找 `WebViewClient`」在 Android 15 上不成立，
   以后写 WebView hook 必须走 `getWebViewProvider()`。
3. 探针在 Gaia 里跑，`getAllApkPaths()` 只有 **1 条**（`.../gaia/data/app/com.sandbox.probe/base.apk`），
   我们容器里是 4~10 条 —— 又一个「容器自己加东西」的证据，但和本次 MIME 问题无关。
