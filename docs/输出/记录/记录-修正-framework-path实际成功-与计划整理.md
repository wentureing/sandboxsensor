# 修正：`bindViaFrameworkLoadedApk` **实际是成功的** —— 我之前的"必然失败"判断错了

- 日期：2026-11-24 16:22
- 起因：用户要求"调查如果使用方案 B，其他软件怎么跑" + 整理计划
- **结论：我关于"framework path 必然失败"的判断是第 4 次过早定性，实际它是成功的**

---

## 一、breadcrumb 的铁证（`breadcrumb-28055.log`）

```
[+0ms]  [main] createFrameworkLoadedApk call pkg=mark.via appInfo.packageName=mark.via
                appInfo.className=mark.via.BrowserApp
                appInfo.sourceDir=.../apks/mark.via_1795488613994.apk

[+0ms]  [main] createFrameworkLoadedApk result loadedApk=#34683083
                mPackageName=mark.via
                ai.packageName=mark.via
                ai.sourceDir=.../apks/mark.via_1795488613994.apk
                mApplication=null                          ← guest 的 clone，mApplication 是 null！
                mPackages[guest]=#34683083
                mPackages[host]=#145045672                 ← 容器的 LoadedApk 是另一个对象

[+38ms] [main] replaceClassLoader host=#145045672 guestCL=#58234535
[+38ms] [main] replaceClassLoader done pkg=mark.via target=#245389053
                mPackages[guest]=#245389053 mPackages[host]=#145045672

[+52ms] [main] bind start instance=mark.via_1795488613994 appClass=mark.via.BrowserApp
                providers=2 frameworkLoadedApk=true
[+53ms] [main] framework makeApplication enter
[+53ms] [main] framework bind identity loadedApk=#34683083 mPackageName=com.nextvm.app
                ai.packageName=mark.via mApplication=null   ← 注意 mPackageName 已被 patch 成 host
[+56ms] [main] instr newApplication mark.via.BrowserApp clId=58234535
[+58ms] [main] framework makeApplication exit app=mark.via.BrowserApp    ← 成功！
[+59ms] [main] providers begin n=2 list=androidx.core.content.FileProvider,androidx.startup.InitializationProvider
```

### 关键事实

| # | 事实 |
| --- | --- |
| 1 | `createFrameworkLoadedApk` **为 guest 创建了一个独立的 `LoadedApk`**（`#34683083`），与容器的（`#145045672`）分开 |
| 2 | 这个 guest clone 的 **`mApplication` 是 `null`** |
| 3 | **`framework makeApplication exit app=mark.via.BrowserApp`** → **成功创建了 guest 的 Application** |
| 4 | `mPackageName` 被 patch 成 `com.nextvm.app`（为了 `AppOpsManager.checkPackage` 通过），**但 `ai.packageName` 仍是 `mark.via`** |

---

## 二、`Log.wtf` 是**噪音**，不是失败证据

```
E LoadedApk: App instance already created for package=com.nextvm.app
             instance=com.nextvm.app.NextVmApplication@ada75ed
    at android.app.LoadedApk.makeApplicationInner(LoadedApk.java:1467)
    at android.app.LoadedApk.makeApplication(LoadedApk.java:1436)
    at VirtualApplicationManager.bindViaFrameworkLoadedApk(VirtualApplicationManager.kt:1446)
```

### 为什么它是噪音

1. **`mPackageName` 被 patch 成 `com.nextvm.app`** → `Log.wtf` 的文案里写的是这个
2. **但实际创建的是 `mark.via.BrowserApp`**（breadcrumb 明确记录）
3. `Log.wtf` 只是 `Log.wtf` —— **它抛 `TerribleFailure` 但被 `makeApplicationInner` 内部吞掉**，然后**继续创建**

**所以**：
> **`Log.wtf` 的存在 ≠ framework path 失败。**
> **breadcrumb 的 `framework makeApplication exit app=mark.via.BrowserApp` 才是实质结果。**

---

## 三、我错在哪（第 4 次同类错误）

| # | 错误 | 正确做法 |
| --- | --- | --- |
| 1 | `N03`/`N06` 失败 → "重定向生效了" | 判据要选"真实路径存在、沙箱不存在"的样本 |
| 2 | `enableAntiDetection` 搜不到 → "死代码" | 静态搜索 ≠ 运行时观测 |
| 3 | `syscall` 只打日志 → "删掉" | 能力即使当前不用，也可能为未来准备 |
| **4** | **`Log.wtf` 出现 → "framework path 必然失败"** | **日志的严重级别 ≠ 业务结果。必须看 breadcrumb 的实际返回值** |

**共同点**：**看到一个"异常信号"就下结论，没有去核对"业务层实际发生了什么"。**

---

## 四、那"方案 B"到底是什么，其他软件怎么跑

### 4.1 现在实际生效的是 **framework path**（不是 legacy）

```
bindApplication
  ├─ frameworkLoadedApk = createFrameworkLoadedApk(...)   ← 为 guest 单独建一个 LoadedApk clone
  │     └─ mApplication = null
  ├─ bindViaFrameworkLoadedApk(...)                        ← 在 clone 上调 makeApplication
  │     └─ ✓ 成功创建 guest 的 Application（mark.via.BrowserApp）
  │     └─ ✓ setGuestApplication
  │     └─ ✓ initContentProviders
  │     └─ ✓ callApplicationOnCreate
  └─ if (fwApp != null) return fwApp                       ← 走这条，legacy 不执行
```

### 4.2 所以"方案 B（绕过 makeApplication 自己 new）"**不需要做**

**因为**：
- **framework path 用的是 guest 自己的 `LoadedApk` clone** → `makeApplication` 正常工作
- **不需要绕过**

**legacy bind（`createApplication` 自己 new）只是 fallback**，在 `createFrameworkLoadedApk` 返回 null 时才会走。

### 4.3 "其他软件怎么跑" —— **同一个机制**

| 应用类型 | 走哪条路 | 说明 |
| --- | --- | --- |
| **普通应用**（无加固） | framework path | `createFrameworkLoadedApk` 成功 → `makeApplication` 成功 |
| **加固应用**（Jiagu/360） | framework path + 额外的 `patchJiaguStubBeforeAttach` / `installRuntimeLoadCallerFix` | 代码里有专门处理 |
| **Via**（特殊） | framework path（breadcrumb 证明） | 同上 |
| 当 `createFrameworkLoadedApk` 返回 null | legacy `createApplication` | fallback |

**所以**：**方案 B 已经实现，而且是主路径**。**所有应用走同一套**。

---

## 五、那 Via 主页显示源码的真因是什么

**既然 Application 绑对了**，问题就不在 Application 层。

**回到截图**：
- UI dump 只有两个 `TextView`：`homepage2.html` / `1`
- **不是 WebView 渲染**

**可能性**（按优先级）：

| # | 假设 | 怎么验证 |
| --- | --- | --- |
| **1** | **Via 用 `TextView` 显示**（它的"源码查看"模式），因为它认为这不是可渲染的 URL | 查 Via 是否对 `file://` 有特殊处理 |
| **2** | Via 的 WebView 初始化失败，退化到文本显示 | 查 Via 日志里的 WebView 错误 |
| **3** | `shouldInterceptRequest` 返回的 MIME 是 `text/plain` | 需要 Via 自己的日志 |
| **4** | `homepage2.html` 的扩展名/MIME 识别失败 | 查 `MimeTypeMap.getMimeTypeFromExtension("html")` |

**我倾向 #1 或 #4** —— 因为 **UI 里根本没有 WebView 控件**（只有两个 TextView）。

---

## 六、当前计划与顺序（整理）

### 6.1 已确认的事实（本轮新增）

| # | 事实 | 状态 |
| --- | --- | --- |
| 1 | **`bindViaFrameworkLoadedApk` 成功**（guest Application = `mark.via.BrowserApp`） | ✅ 已证实 |
| 2 | **`Log.wtf` 是噪音**（`mPackageName` 被 patch 导致文案误导） | ✅ 已证实 |
| 3 | **UI 里没有 WebView**（只有两个 TextView） | ✅ 截图 |
| 4 | **`startApkWatchdog` 的 `link()` 失败**（`EACCES`） | ✅ 已证实 |
| 5 | **`seccomp USER_NOTIF` 装不上**（`EINVAL`） | ✅ 已证实 |
| 6 | **`mAudioService` 反射过时**（Android 15） | ✅ 已证实 |
| 7 | **双轨沙箱**：UI 读 `app_virtual`（空），真实数据在 `files/virtual` | ✅ 已证实 |
| 8 | **沙箱 `/sdcard` 已有 13 个标准目录** | ✅ 已证实 |

### 6.2 计划顺序（按"用户可见收益 ÷ 风险"重排）

| 顺 | 项 | 工作量 | 风险 | 收益 | 依据 |
| --- | --- | --- | --- | --- | --- |
| **1** | **修双轨沙箱**（`AppModule.kt:228` 改 `File(context.filesDir, "virtual")`） | 1 行 + 验证 | **低** | **用户直接看到 4MB 真实数据** | `记录-双轨沙箱实证` |
| **2** | **修 `startApkWatchdog` 的 `link()`** | 半天 | 低 | 防 guest 删自己 APK 时崩 | 本轮取证 |
| **3** | **P0-3 主进程不装 hook** | 3 行 + 验证 | 低 | 主进程零痕迹；可删 `is_import_read_exempt` | `计划/P0-3` |
| **4** | **P0-4/P0-5 隐藏容器**（含 `readdir` 过滤） | 1.5 天 | 中–高 | 消灭 OWASP package structure 检测面 | `计划/P0-4-P0-5` |
| **5** | **修 `mAudioService` 等过时反射** | 半天 | 低 | 消除警告（可能影响音频功能） | 本轮取证 |
| 6 | **查 Via 主页真因**（#1 假设：Via 自己用 TextView） | 半天调查 | — | 定位后才知道能不能修 | 本轮 |
| 7 | P1-1 SelfCheckRunner | 1.5 天 | 中 | 自检能力 | `计划/P1-1` |
| 8 | P1-3 WebView 代读 | 4–5 天 | 中高 | **暂不执行**（等 Gaia 研究） | `计划/P1-3` |
| 9 | P2 长期重构 | 2–3 天/项 | 中高 | — | `计划/P2` |

### 6.3 明确不做

| 项 | 原因 |
| --- | --- |
| 方案 B（绕过 `makeApplication`） | **framework path 本来就用 guest 的 clone，不需要绕过** |
| 删除纯诊断 hook | 改为加 `NS_HOOK_DIAG_ONLY` 开关 |
| `:probe` 独立进程 | guest 进程本身就是全 hook 环境 |
| L2 示例文件 / L3 共享真实 `/sdcard` | 用户已决定不做 |
