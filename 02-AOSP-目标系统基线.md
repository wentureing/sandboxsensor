# AOSP 目标系统基线（本项目支持范围与规范链接）

- 建立日期：2026-11-21
- 用途：锁定本项目"支持哪些 Android 版本 + 每个版本必须处理的平台限制"，避免追新特性导致返工
- 上游决策：**支持至少 3 个活跃系统版本，不盲目追新**；实现以 AOSP 官方规范为准，不以任何第三方容器的行为为准

---

## 1. 市场分布数据（选型依据）

数据源：Google Android Studio 版本分布（2026-10-01，覆盖**七天内连接过 Google Play 的活跃设备**），经 Heise 整理、安兔兔转述（[原文](https://antutu.com/doc/138226)，2026-11-02）。

| 版本 | 占比 | 备注 |
| --- | ---: | --- |
| Android 16 | **25.4%** | 2025-12 时为 7.5%，一年内 +17.9pt |
| Android 14 | **15.3%** | |
| Android 15 | **14.7%** | |
| Android 13 | 11.8% | |
| Android 11 | 10.5% | 仍有两位数，碎片化严重 |
| Android 12 | 9.6% | |
| Android 10 | 5.7% | |
| Android 9 | 3.1% | |
| Android 8.1 / 8.0 | 1.5% / 0.5% | |
| Android 17 | **1%** | 2026-06 发布，4 个月仅 1% |

汇总：**Android 14 + 15 + 16 = 56.4%**；Android 13 及以下合计 43.6%。

## 2. 本项目的支持范围（决策）

| 层级 | 版本 | API | 定位 |
| --- | --- | --- | --- |
| **主支持 1** | Android 16 | 36 | 当前第一大份额，必须一等公民 |
| **主支持 2** | Android 15 | 35 | 16 KB page size 首波设备 |
| **主支持 3** | Android 14 | 34 | 动态代码加载限制的起点，必须专项处理 |
| 降级支持 | Android 13 | 33 | 尽力兼容，不承诺全部能力（占 11.8%） |
| **不支持** | Android 17 | 37 | 仅 1%，不投入；不为其做前瞻适配 |
| 不支持 | Android 12 及以下 | ≤32 | 占 30% 但平台差异过大，明确写在"不支持"清单 |

- `minSdk` 建议 30（Android 11）仅作编译下限；**功能验收只在 33/34/35/36 上做**
- `targetSdk` 建议跟随主支持最高版本（36），以获得确定的行为模型；不要靠 `targetSdk` 低版本来"规避"平台限制（Play 政策 + 系统会逐步强制）
- 双 ABI：`arm64-v8a`（必需）+ `x86_64`（模拟器调试）；`armeabi-v7a` 视目标用户决定
- **16 KB page size 必须支持**（见 3.4）

---

## 3. 逐版本必须处理的平台限制（对本项目有直接影响）

> 以下每条都直接落在容器的实现路径上。实现前请打开官方页面逐条核对（我仅列出与本项目相关的部分）。

### 3.1 Android 14（API 34，targetSdk 34）

| 变更 | 对本项目的直接影响 | 处理要求 |
| --- | --- | --- |
| **动态代码加载必须只读**（DCL：动态加载的 DEX/JAR/APK 必须以只读方式打开） | 原文（`a14-behavior-14.txt`）："If your app targets Android 14 (API level 34) or higher and uses Dynamic Code Loading (DCL), **all dynamically-loaded files must be marked as read-only**" → guest APK / split / 插件 APK / 解压出的 dex 若以可写方式打开会直接抛异常 | 所有加载源一律 `MODE_READONLY`；禁止"写完再加载"的可写句柄；APK 复制到沙箱后立即改为只读 |
| 前台服务类型必须声明（FGS types） | guest 的前台服务在宿主 manifest 下运行，宿主必须声明对应 type | 宿主 manifest 预留 FGS types；映射 guest 的 `foregroundServiceType` |
| 隐式 Intent / 运行时注册广播限制 | guest 组件路由依赖显式 Intent | 占位组件路由必须使用显式 Intent（本来就是），避免依赖隐式解析 |

### 3.2 Android 15（API 35，targetSdk 35）

| 变更 | 影响 | 处理要求 |
| --- | --- | --- |
| **16 KB page size 设备**（见 3.4） | 自研 native 库与 guest SO 都必须 16 KB 对齐 | 构建时开启 16 KB 对齐；guest SO 不满足时启动前给结构化错误 |
| targetSdk 35 强制 edge-to-edge | guest 首屏布局与状态栏避让可能异常 | 宿主窗口 insets 透传；提供兼容开关 |
| 后台启动 Activity（BAL）进一步收紧 | 容器从后台拉起 guest Activity 会被拦截 | 见 3.5 的悬浮窗豁免方案 |
| 前台服务超时 / 数据同步类型限制 | guest 长任务型 Service 可能被杀 | 映射到合适的 FGS type；超时前降级 |

### 3.3 Android 16（API 36，targetSdk 36）

| 变更 | 影响 | 处理要求 |
| --- | --- | --- |
| 强制 edge-to-edge + **忽略 orientation / resizability / aspect ratio 限制** | 原文："For apps targeting Android 16 (API level 36), orientation, resizability, and aspect ratio restrictions are ignored" → guest 声明 `screenOrientation` / `resizeableActivity=false` **不再生效** | 容器必须自己实现方向/尺寸策略；不要依赖 guest manifest 的方向声明 |
| **non-SDK 接口限制更新**（targetSdk 36） | 容器重度依赖 @hide API（`ActivityThread`、`LoadedApk`、`IActivityManager` 等） | 统一走 `hiddenapibypass`（Apache-2.0）一类方案；**每版本重新验证反射字段名**；把反射失败做成可诊断错误而非崩溃 |
| JobScheduler / FGS 行为调整 | guest 后台任务调度语义变化 | 容器内本地调度 + 明确降级说明 |

### 3.4 16 KB page size（跨 15/16 的硬性要求）

- 原文核实（`_ref/aosp/raw/page-sizes-16kb.txt`）：
  > "all apps targeting Android 15 (API level 35) and higher must support 16 KB memory page sizes on 64-bit devices on Google Play. **Starting February 1, 2027**, if your app updates don't support 16 KB memory page sizes, you won't be able to release these updates."
- 即：**targetSdk 35+ 的支持要求已生效**；**2027-02-01 是执行点**——届时不支持 16 KB 的更新将无法发布。另注："Without recompiling, apps won't work on 16 KB devices in future Android releases."
- 对我们的含义：本项目 targetSdk 36，**必须现在就支持**，不存在"以后再说"
- 对容器的额外影响：**从 guest APK 中抽出的 `.so` 也必须能被 16 KB 设备加载**；不满足时要在启动前返回结构化错误，而不是运行到 SIGSEGV
- 自研 native 库：NDK 链接参数开启 16 KB 对齐，arm64-v8a 与 x86_64 都要验证
- 官方：[支持 16 KB 页面大小](https://developer.android.com/guide/practices/page-sizes)、[Play 兼容性要求公告（2025-05-08）](https://android-developers.googleblog.com/2025/05/prepare-play-apps-for-devices-with-16kb-page-size.html)

### 3.5 后台启动 Activity（BAL）与容器"从后台拉起 guest"的解法

- 从 Android 10 起后台应用不能随意启动 Activity，Android 15/16 继续收紧
- 容器场景：用户在桌面点图标（前台，没问题）；但 AI/自动化/通知唤起时宿主可能在后台
- **实测可行方案（NestSpace README 记录）**：宿主申请**悬浮窗权限**，持有一个 1×1、透明、不可聚焦的窗口，从而获得后台启动 Activity 的豁免；未授权时启动接口返回 `LAUNCH_BLOCKED` 而不是静默失败
- 该方案属于"平台允许的豁免路径"，不是绕过；要在 PRD 中写明依赖悬浮窗权限

### 3.6 存储与权限（全版本）

- Scoped storage：`/sdcard` 直接路径访问受限 ⇒ 容器的"模拟 sdcard"应落在宿主私有目录，不映射真实外部存储（ShitJumpingVM 即如此）
- `MANAGE_EXTERNAL_STORAGE` 不建议申请（Play 政策风险）
- 宿主 UID 共享 ⇒ guest 会继承宿主权限，**宿主 manifest 权限越少，容器越"干净"**，同时 guest 需要的权限要靠宿主预先声明（这是"权限伞"问题的另一面）
- 官方：[应用沙箱](https://source.android.com/docs/security/app-sandbox)

### 3.7 non-SDK 接口限制（全版本机制）

- Android 9 起引入 non-SDK 接口限制（灰名单/黑名单），Android 11 起收紧，Android 16 对 targetSdk 36 再次更新
- 容器对 `ActivityThread` / `LoadedApk` / `ActivityManager` 内部字段的访问全部依赖反射 ⇒ 必须：
  1. 用统一的 bypass 层（不散落在业务代码里）
  2. 每版本回归测试反射目标是否仍存在
  3. 反射失败降级为**可诊断能力缺失**（`unsupported`），不允许直接崩溃
- 官方：[非 SDK 接口限制](https://developer.android.com/guide/app-compatibility/restrictions-non-sdk-interfaces)、[Android 16 的 non-SDK 更新](https://developer.android.com/about/versions/16/changes/non-sdk-16)

---

## 4. 规范链接清单（**已本地归档**）

> **归档状态：11/11 成功**。`developer.android.com` / `source.android.com` 直连与本机 7897 代理均失败（沙箱内 Schannel 报 `SEC_E_NO_CREDENTIALS`），改用 **`127.0.0.1:12450` + Node（OpenSSL）+ HTTP CONNECT** 抓取成功。
>
> - 抓取脚本：`tools/fetch-aosp.mjs`（`node tools/fetch-aosp.mjs 127.0.0.1:12450`，自动跟随 Google 的 `oauth2authorize` 跳转链）
> - 正文提取：`tools/html2txt.py`（HTML → 纯文本，便于 grep）
> - 归档目录：`_ref/aosp/raw/`（11 个 `.html` + 11 个同名 `.txt`）
>
> 后续核对规范时**优先读本地 `.txt`**，不要再依赖联网。

| 主题 | 归档文件（`_ref/aosp/raw/`） | 原文链接 |
| --- | --- | --- |
| 16 KB 页面大小 | `page-sizes-16kb.txt` | https://developer.android.com/guide/practices/page-sizes |
| 非 SDK 限制总纲 | `non-sdk-restrictions.txt` | https://developer.android.com/guide/app-compatibility/restrictions-non-sdk-interfaces |
| Android 16 non-SDK 更新 | `non-sdk-16.txt` | https://developer.android.com/about/versions/16/changes/non-sdk-16 |
| 应用沙箱 / UID | `app-sandbox.txt` | https://source.android.com/docs/security/app-sandbox |
| Android 14 全应用变更 | `a14-behavior-all.txt` | https://developer.android.com/about/versions/14/behavior-changes-all |
| **Android 14 targetSdk 34 变更** | `a14-behavior-14.txt` | https://developer.android.com/about/versions/14/behavior-changes-14 |
| Android 15 全应用变更 | `a15-behavior-all.txt` | https://developer.android.com/about/versions/15/behavior-changes-all |
| **Android 15 targetSdk 35 变更** | `a15-behavior-15.txt` | https://developer.android.com/about/versions/15/behavior-changes-15 |
| Android 16 全应用变更 | `a16-behavior-all.txt` | https://developer.android.com/about/versions/16/behavior-changes-all |
| **Android 16 targetSdk 36 变更** | `a16-behavior-16.txt` | https://developer.android.com/about/versions/16/behavior-changes-16 |
| 后台启动 Activity | `bal-restrictions.txt`（现标题为 *Activity security*） | https://developer.android.com/guide/components/activities/background-starts |

> 注意：分版本的 `behavior-changes-1X`（targetSdk 相关）才是真正的约束来源，`behavior-changes-all` 只含全应用变更。上文第 3 节的**动态代码加载只读**就只在 `a14-behavior-14.txt` 里，`behavior-changes-all` 中查不到——最初按后者检索会漏掉。

---

## 5. 设备与验证矩阵（基线）

| 维度 | 取值 |
| --- | --- |
| 系统 | Android 14 / 15 / 16（API 34/35/36）为必须；Android 13（33）为尽力 |
| ABI | arm64-v8a（真机）、x86_64（模拟器） |
| page size | 4 KB 与 16 KB 设备各至少 1 台/1 镜像 |
| 显示 | 手机 + 折叠/平板（验证 Android 16 忽略方向/resizability 的影响） |
| 目标 App 类别 | ①普通单进程 ②多进程（含 `android:process` 子进程）③WebView 为主 ④带 native SDK ⑤带加固（预期失败，用于确认诊断口径） |

## 6. 与 NestSpace 验证范围的差异（必须记住）

NestSpace 官方验证范围是 **Android 15 / 16 / 17**（v1.0.7 Release Notes）。本项目要求 **14 / 15 / 16**，因此：

- **不能把 NestSpace 在 15/16 上的结论直接当作 14 上可用**；Android 14 的 DCL 读只读限制是它文档中未强调的项，需要独立验证
- 我们**不需要** Android 17 的适配投入（省掉一部分版本分支）
- 反过来，NestSpace 对"16 KB page size 兼容"和"槽位复用前结束旧进程"的处理值得直接借鉴
