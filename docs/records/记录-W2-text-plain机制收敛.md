# 记录：W2「text/plain」的机制收敛（v1.11 → v1.20）

- 日期：2026-11-24
- 设备：`10AFA8169Z0039G`（vivo V2463A / Android 15 / API 35）
- 探针：v1.11 → v1.20 逐级排除
- 容器：`1.0.0-33b8e4b`（v137 client 包装 + v138 进程 Application）

---

## 一、现象

容器内 WebView 用 `file://` 加载 guest 自己写的 `.html` → `document.contentType = text/plain` →
页面显示源码（Via 主页即此现象）。而 `/data/local/tmp` 下的同一个 HTML → `text/html` 正常渲染。

---

## 二、逐层排除（每一条都有实测）

| # | 假设 | 结论 | 证据 |
| --- | --- | --- | --- |
| 1 | MIME 表坏了 | ❌ | W9/W10：`MimeTypeMap` / `URLConnection` 都返回 `text/html` |
| 2 | MIME 来自内容嗅探（Java） | ✅ 是**曾经的**现象 | W20：内容改成 `GIF89a` 开头 → `image/gif` |
| 3 | 是「谁写的文件」 | ❌ | W17：adb 放进沙箱的文件 → 仍 `text/plain` |
| 4 | 是 `virtual/data` 路径段 | ❌ | W18：容器私有目录根 → 仍 `text/plain` |
| 5 | 是 BOM | ❌ | **W35 四组合**（见下） |
| 6 | Java `AndroidProtocolHandler` 参与 | ❌ | **W34**：`open(GURL)` 对沙箱与 tmp **都返回 null** → Java 完全不参与 |
| 7 | 容器 patch 了 `libwebviewchromium` | ❌ | logcat 无任何相关 `GOT patch` 记录 |
| 8 | 容器注册了路径重定向规则 | ❌ | logcat 无 `Path redirect added` |

### W35 四组合（把 BOM 这个干扰项彻底排掉）

| 组合 | 位置 | BOM | `contentType` |
| --- | --- | --- | --- |
| W2 | 沙箱 | 无 | `text/plain` |
| **W35a** | 沙箱 | **有** | **`text/plain`** |
| W14 | `/data/local/tmp` | 有 | `text/html` |
| **W35b** | `/data/local/tmp` | **无** | **`text/html`** |

**→ 与内容无关，变量就是「路径落在哪」。**

### W34 的真实方法签名（顺带纠正了两处错误认知）

```
AndroidProtocolHandler 实际只有 6 个方法（全部 Java 实现，无 native → RegisterNatives 用不上）：
  open(GURL) -> InputStream                 ← 参数是 GURL，不是 String
  getMimeType(InputStream, GURL) -> String
  a/b/c/d 为资源查找辅助
```

`open(GURL)` 对**两条路径都返回 null** → 普通 `file://` 根本不经过 Java `AndroidProtocolHandler`。

---

## 三、当前假设（待 W36 验证）

> **WebView 对「落在自己 dataDir 下的 `file://`」走特殊分支（不按扩展名）；对「外部路径」走标准 `FileURLLoader`（按扩展名）。**

它能同时解释三件事：

| 观察 | 解释 |
| --- | --- |
| W2 沙箱 → `text/plain` | 沙箱路径 = 容器改写后的 `ApplicationInfo.dataDir`（`injectGuestPathsIntoLoadedApk`）→ 命中「自己家」分支 |
| W14 `/data/local/tmp` → `text/html` | 不在 dataDir 下 → 标准 `FileURLLoader` → 按扩展名 |
| **Gaia 正常** | 它给 WebView 的 dataDir 是 **guest 官方路径**（`/data/user/0/<guest>`），与真实文件位置（`.../gaia/data/user/0/<guest>/...`）**不一致** → 走标准分支 |

**注意**：v138 改了 `ActivityThread.mInitialApplication`，但 `injectGuestPathsIntoLoadedApk` 仍把
`ApplicationInfo.dataDir` 指向沙箱 → W2 仍是 `text/plain`。这与假设自洽（也能反证 v138 没白做：
W3/asset 已修好）。

### 若假设成立，修法如下（不需要"翻译路径"）

| 项 | 值 | 说明 |
| --- | --- | --- |
| **文件 URL** | 仍是**真实沙箱路径**（Via 用 `getFilesDir()` 生成） | 读取走同进程 native hook 直通 → 能读到 |
| **`LoadedApk.mApplicationInfo.dataDir`** | 改成 **guest 官方路径**（`/data/user/0/<guest>`，不存在也没关系） | 只是给 framework 内部做「归属判断」用 |
| 应用侧视角 | `VirtualContext.getDataDir()/getApplicationInfo()` 保持返回沙箱 | 应用读自己文件不受影响 |

即「**给 framework 看一套、给应用看一套**」，与容器既有的 `VirtualContext` / `LoadedApk` 分裂设计一致。

---

## 四、寿命提醒（重要）

这条链属于 **C 层（WebView/framework 内部实现）**：

| 依赖 | 有效期 |
| --- | --- |
| `AndroidProtocolHandler` 的方法签名（`GURL`） | **约 2–3 个月**（WebView 六周发版，Google Play 独立更新） |
| 「dataDir 归属 → 分支」这个行为 | 随 WebView 版本可能变，无公开文档保证 |
| `LoadedApk.mApplicationInfo.dataDir` 字段 | 1–2 个大版本（A 层偏 B 层） |

**→ 即使修好，也要标记为「易碎补丁」**：失效表现是「Via 主页又变源码」，可通过
`v137/v138/W36` 这类 breadcrumb 快速定位。

---

## 五、没有现成开源方案

| 来源 | 结论 |
| --- | --- |
| VirtualApp / Bcore / BlackBox | 靠**路径保真**绕过，不处理 MIME |
| 针对「容器内 file:// MIME」的专项项目 | **不存在** |
| 通用文章（如火山引擎那篇） | 结论一致（WebView 严格按 Content-Type 渲染），但解法只适用于 HTTP 头 |

所以这条路只能自研：要么**代读 + 补 MIME**（W11 已验证可行，但 v137 的 client 包装有**时序问题**：
Via 在 onCreate 里 `loadUrl`，视图树扫描最早到 onResume，永远晚一步），要么走本文假设的 **dataDir 不对齐**。
