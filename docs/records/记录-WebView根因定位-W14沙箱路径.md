# 记录：W14 把 WebView 根因锁定到「沙箱路径」（2026-11-24）

> 接续 `docs/records/交接-20261124-WebView调查.md`。本文只记录这一轮的新事实。

---

## 一、实验条件

| 项 | 值 |
| --- | --- |
| 设备 | `10AFA8169Z0039G`（vivo V2463A / Android 15 / API 35 / arm64-v8a） |
| 容器 | `1.0.0-c00f1cb`（**firstInstallTime 19:36:19，沙箱为空 —— 交接文档的「卸载重装」这步已不需要**） |
| 探针 | v1.11（`dist/probe-6cf199e-arm64-v8a.apk`），容器内实例 `com.sandbox.probe_1795520323559` |
| WebView | `com.google.android.webview` 126.0.6478.71 |
| guest 进程 SELinux | `u:r:untrusted_app:s0:c77,c257,c512,c768` |
| 结果时间片 | 设备时间 `19:38:53`–`19:38:55`，`logcat -s WVP` |

> ⚠️ 探针 v1.11 的日志头仍打印 `WebViewProbe v1.10`（版本字符串硬编码没跟着改）。
> 看到 `v1.10` 不等于跑了旧包 —— 判据是 **W14/W15/W16 有没有出现在日志里**。v1.12 已修正该字符串。

---

## 二、结果（同一 WebView 实例、同一进程，唯一变量是路径）

| 实验 | URL | 结果 | 判读 |
| --- | --- | --- | --- |
| W1 | `loadDataWithBaseURL(about:blank)` | `body="WVPAGE_OK hello from probe"` | 渲染链路正常 |
| W2 | `file:///data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_1795520323559/files/wvprobe.html` | `body="<html>...WVPAGE_OK..."`（**源码**） | ❌ MIME 丢失 |
| W3 | `file:///android_asset/wvprobe.html` | `body="EMPTY"` | ❌ asset 打不开 |
| W4 | 沙箱内另一个文件名 `.html` | 源码 | ❌ 一致 |
| **W14** | **`file:///data/local/tmp/wvprobe_tmp.html`** | **`body="W14_TMP_OK from data local tmp"`** | ✅ **正常渲染** |
| W15 | 沙箱路径 + `?x=.html` | 源码 | ❌ query 不影响判定 |
| W16 | 沙箱 `.txt`（内容是 HTML） | 源码 | ❌ 一致 |
| W11 | 沙箱路径 + `shouldInterceptRequest` 主动返回 `text/html` | ✅ 正常渲染（`[intercepted 1]`） | 方案 A 有效 |

**三条新事实**：

1. **F-A**：`/data/local/tmp` 下的 HTML **在容器内能正常渲染** —— 同一个 WebView、同一进程、同一个 `libwebviewchromium`。
   → 排除「WebView 在容器进程里的整体环境坏了」这一整类解释。
2. **F-B**：差异**只在沙箱路径**上。所有沙箱路径（`wvprobe.html`、`wvprobe_w4.html`、`.txt`、加 `?x=`）全部失败。
3. **F-C**：`?x=.html` 不改变结果 → **不是"按 URL 末段扩展名判 MIME"的问题**（若按末段，`?x=.html` 应命中 html）。

---

## 三、本轮排除的机制（有证据）

| 假设 | 证据 | 结论 |
| --- | --- | --- |
| native hook patch 了 `libwebviewchromium` | `core/hook/src/main/cpp/native-hook.cpp:1654` `NS_HOOK_SYSTEM_LIBS` 默认 **0**，日志里也没有 `GOT patch ... webviewchromium` | 排除 |
| 容器 Java 层 hook 了 `shouldInterceptRequest` / `WebResourceResponse` / `MimeTypeMap` / `getFileExtensionFromUrl` | `grep` 全仓 `_ref/ShitJumpingVM`：`shouldInterceptRequest`/`WebResourceResponse`/`AndroidProtocolHandler`/`MimeTypeMap` 全部 **0 命中** | 排除 |
| MIME 表被破坏 | W9 `getMimeTypeFromExtension("html")=text/html`、W10 `guessContentTypeFromName("x.html")=text/html` | 排除 |
| guest 读不到 `/data/local/tmp` | W14 渲染成功（`untrusted_app` 域靠容器的 import 豁免放行，见 `docs/records/记录-导入豁免安全审查.md`） | 排除 |
| Chromium 日志里有 MIME 线索 | `logcat -s chromium` 无 mime/text-plain 记录 | 无信息（需 `document.contentType` 才能确认） |

**剩下未验证的关键分叉**（v1.12 的 W17/W18/W19 就是冲这个去的）：

- **是不是「谁写的文件」**：W2/W4/W15/W16 的文件都是**探针自己写的**；W14 的是 **adb push** 的。
  若容器对"guest 写入沙箱"的文件做了某种特殊标记/重定向，那变量就不是路径而是写入者。
- **是不是 `virtual/data/<instance>` 这一段路径**：W18 写到 `/data/user/0/com.nextvm.app/files/`（容器私有目录根，不经 virtual）。
- **重定向路径**：W19 用 `/sdcard/`（被容器重定向到沙箱）—— 若它也失败，说明"凡是经过容器重定向的路径"都丢 MIME。

---

## 四、对修复方案的影响

**W14 之前**有两条互斥的路：

- 根因在**路径映射** → 修 `VirtualContext`/路径重定向，不碰 WebView
- 根因在**进程环境** → 只能加 `shouldInterceptRequest` hook（方案 A）

**W14 之后**：根因确实落在「路径」这一侧（F-A + F-B），所以**仍然值得先弄清机制再定修法**，
因为能力上差很多：

| 若机制是 | 修在哪里 | 影响面 |
| --- | --- | --- |
| 沙箱路径的 MIME 判定（例如 Chromium 对容器路径拿不到 `stat`/扩展名） | 容器路径映射层 | 一次修好，Via/所有 guest 都受益，不动 WebView |
| 只有"guest 写入的文件"特殊 | 容器写入路径 | 需先搞清对 Via 主页（Via 自己写 homepage2.html）是否同样命中 —— **Via 的主页正是应用自己写的**，这条最贴合 Via 的现象 |
| 重定向路径普遍丢 MIME | 重定向层 | 同上 |

> **注意**：Via 的主页是 Via **自己写**到 `getFilesDir()` 再 `loadUrl("file://...")` 的
> （见 `docs/records/记录-反编译Via主页加载全流程.md`），所以「写入者」这条假设完全符合 Via 的现象，
> 不能跳过。这正是 W17 存在的理由。

---

## 五、下一步（v1.12）

探针 v1.12（本次改动，未构建）：

- `W17`：`file://<filesDir>/pushedup.html`，文件由 **adb 侧**放入（探针只观察 `exists/len`）
- `W18`：`file:///data/user/0/com.nextvm.app/files/wvprobe18.html`（容器私有目录根）
- `W19`：`file:///sdcard/wvprobe19.html`（被容器重定向的公共目录）
- 所有实验的 `onPageFinished` 追加 **`document.contentType`**（直接读出 WebView 判定的 MIME）
- `brief()` 上限 160 → 300（否则 JSON 里的 body 会被截掉）
- 版本字符串修正为 v1.12；`versionCode 12 → 13`

判读表：

| W17 | W18 | W19 | 结论 |
| --- | --- | --- | --- |
| 正常 | — | — | 变量是**写入者**（探针写的沙箱文件有问题）→ 查容器写入路径 |
| 源码 | 正常 | — | 变量是 **`virtual/data/<instance>` 路径段** |
| 源码 | 源码 | 正常 | 变量是 **重定向**（/sdcard 走另一条路） |
| 源码 | 源码 | 源码 | 凡是容器管的路径都丢 MIME → 方案 A 是唯一稳妥解 |

无论哪种，**方案 A（`shouldInterceptRequest` 补 MIME）都能修好 Via**，W11 已验证；
所以 v1.12 的结论只决定"要不要额外做一层更底层的修复"。

---

## 六、设备当前状态（交接要点）

```
容器 com.nextvm.app  1.0.0-c00f1cb，沙箱内有且仅有：
  com.sandbox.probe_1795520323559（探针 v1.11）
/data/local/tmp 里可用：probe-v111.apk、via.apk、wvprobe_tmp.html
```

- **Via 还没导入**（沙箱是新装的）
- 容器内探针无法原地升级（`INSTALL` 对同名包是跳过）→ 装 v1.12 前先
  `adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.UNINSTALL --es pkg com.sandbox.probe`

---

## 七、复现命令

```powershell
# 1) 导入探针
adb -s 10AFA8169Z0039G shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path /data/local/tmp/probe-v111.apk
# 2) 跑实验（约 3 秒跑完全部 W 系列）
adb -s 10AFA8169Z0039G shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH_ACTIVITY --es pkg com.sandbox.probe --es activity com.sandbox.probe.WebViewProbeActivity
# 3) 取结果
adb -s 10AFA8169Z0039G logcat -d -s WVP
```
