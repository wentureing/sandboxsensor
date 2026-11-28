# 册：WebView 与 `file://` MIME（2026-11-27 定稿）

- **时效**：2026-11-27 复核。下次复核触发：① 容器换 WebView 实现（内置/AssetLoader）② Android 大版本升级 ③ 容器包名变更（会影响路径判定）
- **可信度**：结论 A（实测，探针 W 系列）；机制解释 B（代码/文档推理）
- **替代**：本文取代 `输出/记录/` 下 8 篇 WebView 记录 + `输出/调研/调查-WebView资源加载流程与多版本兼容.md` 的**结论部分**（原文保留作证据，见 §五）
- 一句话：**`file://` 的 MIME 由「URL 里是否出现应用包名」决定**；把内容放到 `/storage/emulated/0/<不含包名的目录>/` 即 `text/html`。

---

## 一、现行结论（A 级，实测）

| # | 结论 | 证据 |
| --- | --- | --- |
| **W-a** | 沙箱路径（`/data/user/0/com.nextvm.app/…`，含包名）的 `.html` → **`text/plain`** | W2（多次复现） |
| **W-b** | `/data/local/tmp/…`、`/sdcard/<file>`、`/storage/emulated/0/Download/…`（**不含包名**）→ **`text/html`** | W14 / W19 / W-ext-public |
| **W-c** | 外部私有目录 `/storage/emulated/0/Android/data/com.nextvm.app/files/`（含包名）→ **`text/plain`** | W-ext-private |
| **W-d** | 自建目录但**目录名含包名** `/storage/emulated/0/com.nextvm.app/` → **`text/plain`** | W-ext-shared |
| **W-e** | 自建目录且**不含包名** `/storage/emulated/0/vsandbox/` → **`text/html`** | **W-ext-nopkg（定稿依据）** |
| **W-f** | MIME 判定在 **Chromium 内核**（`FileURLLoaderFactory`），**不在 Java 层**：`AndroidProtocolHandler.open()` 对两条前缀都返回 null | W34 |
| **W-g** | `file:///android_asset/` 在容器里打不开（`Unable to open asset URL`），容器外正常 | W3 对照组 |

**规律一句话**：`file://` URL 里**含应用包名** ⇒ Chromium 走内容嗅探（HTML 无魔数）⇒ `text/plain`；**不含** ⇒ 按扩展名 ⇒ `text/html`。

---

## 一之二、必须分清的三段（2026-11-27 修订）

之前把问题笼统说成"guest 看到的路径"，这是不准确的。`file://` 的 MIME 取决于**三段**里的第三段，而容器只能在第二段插手：

| 段 | 定义 | 举例 | 谁能改 |
| --- | --- | --- | --- |
| **① 发起路径** | guest 代码拼出来、交给 `WebView.loadUrl()` 的那个 URL 字符串 | `file://` + `getFilesDir()` + `/homepage2.html` | guest 自己（我们只能改 `getFilesDir()` 的返回值） |
| **② 中间转换（隐含）** | WebView/Chromium 解析 URL 到"真正去读哪个文件"的过程里，容器**还能不能插手** | 沙箱路径要不要翻译？`/sdcard` 要不要重定向？ | 容器（路径规则 / hook）——**但对 Chromium 内部读取无效**（`libwebviewchromium.so` 不 patch） |
| **③ 接受路径** | Chromium 实际 `open()` 的文件路径，**MIME 判定看的是它** | `/data/user/0/com.nextvm.app/files/virtual/.../x.html` | 不由我们直接控制 |

**关键结论（据此重排设计）**：

- 第 ② 段对 **`file://` 几乎无效**：Chromium 直接读文件系统，**不经过容器的 GOT patch**（`libwebviewchromium` 在"永不 patch 的图形栈/系统库"之列），
  所以"合成路径 → 容器翻译成沙箱"这条路**在 `file://` 上不成立**（文件读不到，或读到的是真实路径）；
- 因此**能选的只有第 ① 段**：让 guest 拿到的路径本身就是"真实存在、且形态合适"的路径；
- 而"形态合适"的判据 = 第 ③ 段不含**宿主包名**（含了就走嗅探）。

### 待验证的 4 个实验（决定"能不能像真机"）

| 实验 | ① 发起路径 | 要看什么 | 若结果是这样，含义 |
| --- | --- | --- | --- |
| `W-ext-synthpath` | `file:///data/user/0/com.sandbox.probe/files/wv_synth.html`（**合成/guest 形状**） | 能否读到（`EMPTY`/`ERR_FILE_NOT_FOUND` vs 正常） | 读不到 ⇒ 第②段确实无效，**"像真机"不可行** |
| `W-ext-guestpkg` | `file:///storage/emulated/0/vsandbox/com.sandbox.probe/wv_gp.html`（**真实路径 + guest 包名**） | `contentType` | `text/html` ⇒ 嗅探只针对**宿主**包名，"像真机命名"可行；`text/plain` ⇒ 任何含包名都不行 |
| （已有）`W2` | 沙箱真路径（含**宿主**包名） | `text/plain` | 宿主包名触发嗅探 |
| （已有）`W-ext-nopkg` | `/storage/emulated/0/vsandbox/wv.html`（无包名） | `text/html` | 无包名 ⇒ 按扩展名 ✅ |

---

## 二、已证伪的路线（不要再试）

| 路线 | 为什么不行 | 证据 |
| --- | --- | --- |
| 包 `WebViewClient` / `shouldInterceptRequest` 补 MIME | ① `shouldInterceptRequest` 官方只拦 http/https；② via 在 `onCreate` 就 `loadUrl`，视图树扫描永远晚一步（`installed=1` 在 +10s） | v137 已 revert（`ad90b05`） |
| 让 `getDataDir()` 返回 guest 路径（假 dataDir） | `dataDir` 变了，W2 仍 `text/plain` | W37 |
| 把沙箱迁到**外部存储**（笼统说法） | 当时以 W19 `/sdcard` 写入 EPERM 判"无效"，但真正原因是"哪里写"没分清；**外部 `Android/data/<pkg>` 实测仍是 plain** | W-ext-private |
| 迁到"自建目录"（任何自建目录） | 目录名含包名就 plain | W-ext-shared |

---

## 三、现行方案（定稿）

```
内容目录 = /storage/emulated/0/<不含包名的目录>/     例：/storage/emulated/0/vsandbox/
权限     = MANAGE_EXTERNAL_STORAGE（当前 appops=allow；干净安装会重置，脚本重授）
不改动   = WebView 内部、WebViewClient、AssetLoader、内置 WebView —— 一律不需要
副作用   = 路径不含容器包名 ⇒ 对 guest 的暴露面反而更小
```

**待实现**：容器把 guest 的内容目录（`getFilesDir`/`getExternalFilesDir` 的落点，或仅 WebView 用的内容目录）指到
`/storage/emulated/0/vsandbox/<instance>/`，并让路径规则把 guest 视角的路径映射过去。
（实现属"目标 1"的开发项，不在本文范围）

---

## 四、已知可能错 / 待确认（B 级）

| # | 事项 | 说明 |
| --- | --- | --- |
| 1 | "含包名 ⇒ 嗅探"的**机制**解释 | 只是归纳（6 组全部吻合），未读 Chromium 源码确认。若有反例（含包名却 text/html），以反例为准 |
| 2 | 自建目录的**深度/命名**是否还有额外规则 | 只测了 1 层（`/storage/emulated/0/vsandbox/x.html`）；多级目录、非 ASCII 名未测 |
| 3 | 媒体库/扫描器对 `vsandbox` 的干扰 | `Download` 会被媒体库扫；自建目录未测（建议加 `.nomedia`） |
| 4 | Android 16/17 是否改变 | 未在更高版本验证 |

---

## 五、原始文档索引（保留为证据，结论以本文为准）

| 文档 | 贡献 | 状态 |
| --- | --- | --- |
| `输出/记录/记录-WebView受控实验W0W7结果.md` | W0–W7 首批实验 | 历史（结论已并入） |
| `输出/记录/记录-WebView受控实验W8W12结果.md` | W8–W12（含 `shouldInterceptRequest` 能否拦 file://） | 历史 |
| `输出/记录/记录-WebView容器内外对照实验.md` | 容器内/外对照 | 历史 |
| `输出/记录/记录-WebView根因定位-W14沙箱路径.md` | W14 定死"前缀决定 MIME" | 历史 |
| `输出/记录/记录-W2-text-plain机制收敛.md` | **机制收敛**（Chromium 侧判定） | 现行（机制部分仍有效） |
| `输出/记录/记录-Gaia内WebView对照-W2正常与容器差异.md` | Gaia 对照（证明不是平台限制） | 历史 |
| `输出/记录/记录-WebView资源注入导致Via启动循环.md` | 一次失败尝试 | 历史 |
| `输出/记录/记录-离线页面空白的完整证据链.md` / `记录-离线页面调查进度.md` / `记录-via保存离线网页静默失败.md` | via 离线保存链路 | 历史 |
| `输出/调研/调查-WebView资源加载流程与多版本兼容.md` | 加载流程梳理 | 现行（流程），结论以本文为准 |
| `输出/调研/调查-导入代替代读的攻防.md` | asset vs 文件系统区分 | 现行（概念） |
| `架构/架构-WebView假路径换真路径.md` | via 反编译证据（用 `file://`） | 现行（证据） |
| `计划/计划-WebView修复.md` | 早期修复计划 | **作废**（方案已被证伪） |
| `archive/错误结论-2026-11/*WebView*` | 被推翻的假设清单 | 归档 |

---

## 六、取数复现

```powershell
# 通过探针的 WebViewProbeActivity 跑 W 系列（会产出 wvprobe-result.txt）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH_ACTIVITY \
  --es pkg com.sandbox.probe --es activity com.sandbox.probe.WebViewProbeActivity
# 注意：必须先重启容器再直接启动它（否则 slot 1 被回收，见「册-进程与slot」D9）
adb shell run-as com.nextvm.app cat files/virtual/data/<probe实例>/files/wvprobe-result.txt
```
