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

## 四之二、v1.12 实测结果（本轮真正定死机制的一段）

探针 v1.12（`probe-2852d50-arm64-v8a.apk`，实例 `com.sandbox.probe_1795520690390`）。
`onPageFinished` 现在返回 `JSON.stringify({ct, body})`，**`ct` 就是 WebView 自己判定的 MIME**。

| 实验 | URL | **contentType** | body | 说明 |
| --- | --- | --- | --- | --- |
| W1 | `about:blank` | `text/html` | 渲染后的 innerText | 对照 |
| W2 | `file://…/virtual/data/<inst>/files/wvprobe.html` | **`text/plain`** | 源码 | |
| W3 | `file:///android_asset/wvprobe.html` | `text/html` | `EMPTY` | 另一问题（asset 打不开） |
| W4 | 同上另一个 `.html` | **`text/plain`** | 源码 | |
| W14 | `file:///data/local/tmp/wvprobe_tmp.html` | **`text/html`** | 正常 | |
| W15 | 沙箱 + `?x=.html` | **`text/plain`** | 源码 | |
| W16 | 沙箱 `.txt`（内容是 HTML） | **`text/plain`** | 源码 | |
| W17 | 沙箱 `pushedup.html`（**adb 放入**，非 guest 写） | **`text/plain`** | 源码 | **写入者不是变量** |
| **W17′** | 同一个 W17 文件，内容改成 **`GIF89a` 开头** | **`image/gif`** | `EMPTY` | ⭐ **MIME 来自内容嗅探** |
| W18 | `file:///data/user/0/com.nextvm.app/files/wvprobe18.html`（私有目录**根**） | **`text/plain`** | 源码 | 与 `virtual/data` 路径段无关 |
| W19 | `file:///sdcard/wvprobe19.html` | `text/html` | `EMPTY` | **无效**：guest 写 `/sdcard` 报 `EPERM`，文件没创建 |
| W11 | 沙箱 + 主动返回 `text/html` | `text/html` | 正常 | 方案 A |

### 关键实验：把 W14 的文件换成**符号链接**

```powershell
ln -sf '/data/user/0/com.nextvm.app/files/virtual/data/<inst>/files/wvprobe.html' \
       /data/local/tmp/wvprobe_tmp.html
```

结果：

```
W14 url=file:///data/local/tmp/wvprobe_tmp.html -> {"ct":"text/html","body":"EMPTY"}
W2  url=file:///data/user/0/com.nextvm.app/.../wvprobe.html -> {"ct":"text/plain","body":"<html>..."}
```

- 内容 `EMPTY`：符号链接目标在容器私有目录，`/data/local/tmp` 前缀被豁免、不重定向 → 读不到目标（预期）
- **但 `ct` 仍是 `text/html`** → 即使指向的是沙箱里的同一个 inode，只要 URL 前缀是 `/data/local/tmp`，MIME 就按**扩展名**判

**→ 决定 MIME 判定方式的是 URL 路径前缀，不是文件的真实位置/属主/写入者。**

---

## 四之三、机制结论（可落地）

**F-D（定死了）**：沙箱前缀（`/data/user/0/com.nextvm.app/...`）的 `file://` 请求，
MIME **来自内容嗅探** `java.net.URLConnection.guessContentTypeFromStream(stream)`，
而**不是**扩展名。

依据：W17′ 的扩展名是 `.html`，却因为内容以 `GIF89a` 开头被判成 `image/gif`。
`guessContentTypeFromStream` **只认少数魔数**（Java 序列化 `AC ED` / GIF / JPEG / PNG），
**HTML 没有魔数 → 返回 null → 上层用默认 `text/plain`**。

这与 AOSP `AndroidProtocolHandler.java` 的兜底分支**逐行吻合**：

```java
// chromium android_webview/java/src/org/chromium/android_webview/AndroidProtocolHandler.java
} else if (uri.getScheme().equals(FILE_SCHEME)
        && path.startsWith(nativeGetAndroidAssetPath())) {
    String mimeType = URLConnection.guessContentTypeFromName(path);   // ← 只有 asset 路径按名字
    if (mimeType != null) return mimeType;
}
// Fall back to sniffing the type from the stream.   ← 其它一律嗅探
return URLConnection.guessContentTypeFromStream(stream);
```

**F-E**：`/data/local/tmp/...` 前缀的 `file://` 走的是**按扩展名**判定的加载器（native file loader），
所以同一个 HTML 能正常渲染。**同一个 WebView、同一进程，两条前缀走两套 MIME 判定。**

**尚未解释的部分（如实记录）**：为什么容器内「容器 app 私有目录」前缀会落到 Java 的嗅探分支，
而 `/data/local/tmp` 不会。容器源码里：

- `native-hook.cpp` 的 `redirect_path()` 是**字符串前缀替换**（`g_path_redirects`），
  只影响 `open/openat/fopen/stat` 等调用的**入参路径**，不参与 MIME 判定
- `NS_HOOK_SYSTEM_LIBS=0` → `libwebviewchromium` 未被 patch
- 容器 Java 层没有 `AndroidProtocolHandler` / `MimeTypeMap` / `shouldInterceptRequest` 相关 hook

也就是说：**「改路径映射就能修好 MIME」这条路不成立**（MIME 不走路径判定，走的是内容嗅探），
W14 当初给出的"改 VirtualContext 就行"的乐观分支已被本轮结果否掉。

**→ 修复只剩一层可选：在 `shouldInterceptRequest` 补 MIME（方案 A），W11 已验证有效。**

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
