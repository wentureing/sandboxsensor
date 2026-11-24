# 记录：假 dataDir 假设被否证（v143-fix / v146 实测）

- 日期：2026-11-24
- 假设：WebView 对"落在自己 dataDir 内"的 `file://` 文件走特殊 MIME 分支；
  只要让 WebView 认为 `dataDir` 不是文件所在目录，就会落到标准 `FileURLLoader`（按扩展名判 MIME）。
- **结论：假设被否证。假 dataDir 完全生效，MIME 仍然是 `text/plain`。**

---

## 一、实验条件

| 项 | 值 |
| --- | --- |
| 容器 | `1.0.0-047b6f6`（干净安装） |
| 白名单 | 仅 `com.sandbox.probe` |
| 开关 | `debug.nextvm.webviewDataDirFake=1`（默认）、`javaIo=1`、`javaIoTranslate=1` |
| 探针 | v1.22 |

## 二、结果

### 2.1 假 dataDir 确实生效

```
W37 getApplicationInfo().dataDir = /data/user/0/com.sandbox.probe
W37 仍是沙箱路径（说明被 VirtualContext 覆盖）= false
W37 是 guest 官方路径（说明 v139 生效）= true
```

debug-trace 同时确认决策路径：

```
[Dbg/appInfo] #0 detached=true baseDataDir=/data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795533639824 pkg=com.sandbox.probe
[Dbg/v143]   datadir pkg=com.sandbox.probe ... -> /data/user/0/com.sandbox.probe
```

### 2.2 MIME 没有变化

```
W2 url=file:///data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795533639824/files/wvprobe.html
   -> ct: "text/plain"
```

**dataDir 从沙箱路径变成 `/data/user/0/com.sandbox.probe`，`document.contentType` 仍是 `text/plain`。**

### 2.3 其他能力未退化

| 项 | 结果 |
| --- | --- |
| W38（Java 层写 `/sdcard`） | ✅ OK，文件落沙箱 |
| W3（`android_asset`） | ✅ `ASSET_OK` / `text/html` |

---

## 三、结论

| # | 结论 |
| --- | --- |
| 1 | **`dataDir` 归属不是 MIME 判定的变量** —— WebView 不使用"是否在自己 dataDir 内"来决定 MIME |
| 2 | 之前"Gaia 靠路径保真相对于我们靠路径改写所以 MIME 不同"的推断，**至少这条因果链是错的** |
| 3 | 因此**改 dataDir / pathFidelity 都不能治 MIME**，这条路线到此为止 |

## 四、附带发现：v143 原本无效的原因（已修）

v143 原实现把假值写进 **`guest Application.mLoadedApk.mApplicationInfo`**，但
`VirtualContext.super.getApplicationInfo()` 拿到的是 **容器 `ContextImpl` 的 LoadedApk** ——
两个不同对象，注入值没人读（breadcrumb 显示注入成功、W37 读到的仍是沙箱）。

v143-fix 改为：`VirtualContext.getApplicationInfo()` 在 detached 时**直接返回**
`VirtualConstants.syntheticDataDir(guestPkg)`，不再依赖 LoadedApk 注入。本次实验证明该修法有效。

## 五、下一步（回到计划里的另一条路）

按变更计划 `20261124-javaio-expand-and-fake-datadir.md` 的失败判据：

> 停止这条路线，回到代读（`shouldInterceptRequest`）—— 需解决注入时机。

代读的已知状态：

| 项 | 状态 |
| --- | --- |
| 有效性 | **W11 已验证有效**（`shouldInterceptRequest` 返回自造 `WebResourceResponse` 可改 MIME） |
| 阻塞点 | **注入时机**：Via 在 `onCreate`/fragment 里就 `loadUrl`，而包装 client 最早只能到 `onResume`/定时器（v137 实测 `installed=0(+132ms) → 1(+10s)`，`delegated=0`） |
| 未尝试的方向 | ① 在 **ActivityThreadHook 的 hook 点**（Activity 实例化后、`onCreate` 前）包装；② 替换 `WebViewFactory`/`WebView` 构造路径；③ 用 `WebView.setWebViewClient` 的反射字段在 `onCreate` 之前完成 |

## 六、方法论备注

这次是"先写判据、再跑实验"的正面例子：

- 变更计划里预先写了三种判据（W39 失败 / W37 仍沙箱 / W37 变假但 W2 仍 plain）
- 实测落在第三种 → **直接判否，不继续找理由解释**
- 因此省下了"再试几个变体"的时间，也避免把否证结论留在口头
