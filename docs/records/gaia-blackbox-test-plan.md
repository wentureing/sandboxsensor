# 黑盒测试方案：推断 Gaia 的 WebView 支持机制

- 日期：2026-11-23
- 设备：`10AFA8169Z0039G`（vivo V2463A / Android 15）
- 已知：Gaia（`com.app.hider.master.pro.cn`）已装，Via 已导入其沙箱
- 原则：**只测试，不反编译**
- 已推送素材：
  - `/sdcard/Download/wvtest.html`（900 B，橙色方块 + JS 探测，见文末）
  - `/sdcard/Download/probe-3be36b2.apk`（探针 v1.6）

---

## 零、先排除一个障碍：这台设备连 adb 都读不了 Gaia 沙箱

```
$ adb shell ls /sdcard/Android/data/com.app.hider.master.pro.cn/gaia/mark.via/Android/data/mark.via/files
Permission denied
```

**只有顶层目录名可读**（能看到 `mark.via`），内容不可读——Android 11+ scoped storage 对 `adb shell` 也生效。

**所以观察必须靠「在容器内运行的探针」**，这也正好符合"用测试代替反编译"。

---

## 一、五个实验（按信息量排序）

### 实验 1（最强）：探针在 Gaia 内运行 —— 直接读出沙箱路径

**为什么最强**：探针以 **Gaia 的 uid** 运行，能读到自己所在沙箱的全部路径信息，等于"从内部看 Gaia"。

**操作步骤**（你做）：

1. 打开 Gaia → 「添加应用 / 导入 APK」
2. 选 `/sdcard/Download/probe-3be36b2.apk`
3. 导入完成后，点探针图标启动
4. 等 5 秒（探针会自动跑完并写报告）

**我采集**：

```powershell
adb -s 10AFA8169Z0039G logcat -d -b all -v threadtime > probe-reports/gaia-probe.txt
# 提取所有 PROBE / REPORT 分块 -> JSON
python tools/extract-report.py probe-reports/gaia-probe.txt
```

**关键字段与结论映射**：

| 探针 | 如果显示 | 结论 |
| --- | --- | --- |
| **`J01` getDataDir** | `/sdcard/Android/data/com.app.hider.master.pro.cn/gaia/...` | **H1 成立：沙箱在外部存储** |
| | `/data/user/0/com.app.hider.master.pro.cn/...` | H1 不成立（沙箱在内部） |
| `J14` dataDir | 同上 | 交叉验证 |
| **`P09` findLibrary** | 含 `/gaia/` 或 `/sdcard/` | 印证外部路径 |
| `P05` maps hostHits | >0 且含 `hider` | maps 里暴露宿主包名（Gaia 也没做 maps 过滤？） |
| `P21` fd targets | 含 `/gaia/` | fd 层印证 |
| `P17` java.class.path | 是否含 `hider` | 属性层是否泄漏 |

**这一步就能回答"H1 是否成立"**，成本 2 分钟。

---

### 实验 2（决定性）：Via 加载 `/sdcard/Download/wvtest.html`

**目的**：验证 **WebView renderer 能否读外部存储**。这是"沙箱放外部就能解决"这个假设的核心检验。

**操作步骤**（你做）：

1. 在 Gaia 里打开 Via
2. 地址栏输入并访问：
   ```
   file:///sdcard/Download/wvtest.html
   ```
3. 停在该页面不要动

**我采集**：截图 + logcat

**判据**（看页面长相）：

| 页面表现 | 结论 |
| --- | --- |
| 深蓝背景 + **橙色方块** + `JS: 已执行 title=PROBE-WEBVIEW-OK` | ✅ **HTML/CSS/JS 全部正常** → WebView renderer **能读外部存储** → **H1 足以解释 Gaia** |
| 深蓝背景 + 橙色方块，但 `JS: 未执行` | ✅ HTML/CSS 正常，JS 被禁（`setJavaScriptEnabled(false)`）→ 仍是 H1 |
| **显示 HTML 源码文本** | ❌ renderer 读不到外部存储 → **Gaia 靠 hook（H3）**，我们的方向要改 |
| 空白 / 错误页 | 需要看 logcat 具体错误 |

**这是整个方案里最关键的一次点击。**

---

### 实验 3：Via 打开 `file:///sdcard/Download/`

**目的**：区分"能读单个文件"和"能列目录"。

**操作**：地址栏访问 `file:///sdcard/Download/`

**判据**：
- 列出文件列表 → renderer 有外部存储目录遍历能力
- `denied` / 空白 → 只能读已知路径（或需要 `allowDirectoryAccess`）

---

### 实验 4：Via 主页的地址栏内容

**目的**：确定 Via 主页用的是哪种 URL 方案。

**操作**：在 Gaia 的 Via 里点"主页"，保持页面不动。

**我采集**：截图（读地址栏文字）

**判据**：

| 地址栏 | 含义 |
| --- | --- |
| `file:///...` | Via 用本地文件 → 依赖 H1/H3 |
| `http(s)://...` | 主页是网络页 → **与容器无关**，那"Gaia 主页正常"不能证明它有 WebView 支持！ |
| `data:text/html...` | 内存内容 → 也不依赖文件系统 |
| `via://home` | 自有 scheme |

**注意这一条的现实意义**：如果 Via 主页在 Gaia 里是**网络地址**，那"Gaia 让 Via 主页正常"这个观察本身就与 WebView 文件访问无关——我们可能一直在追一个不存在的能力。

---

### 实验 5：logcat 侧写

**目的**：Gaia 是否在日志里暴露了 WebView 相关处理。

**我采集**（你操作时我抓）：

```powershell
adb -s 10AFA8169Z0039G logcat -d -b all -v threadtime > probe-reports/gaia-run.txt
```

**我会搜**：
- `gaia` / `hider` / `Gaia` 相关日志
- `shouldInterceptRequest` / `WebResourceResponse` / `chromium`
- `avc: denied`（SELinux 拒绝）
- `file://` 失败

---

## 二、结论矩阵（不同实验结果的组合 → 不同机制判断）

| 实验1（探针 J01） | 实验2（外部 HTML） | 实验4（主页 URL） | 推断的 Gaia 机制 | 我们的对策 |
| --- | --- | --- | --- | --- |
| 外部路径 | 正常渲染 | `file://` | **H1：沙箱在外部 + renderer 能读** | 照抄：沙箱迁外部（1 行） |
| 外部路径 | 正常渲染 | `http://` | 主页与文件系统无关 | 无需追，Via 主页本来就不依赖 file:// |
| 外部路径 | **显示源码** | `file://` | **H3：Gaia 做了 hook** | 需要逆向/自研，成本高 |
| 内部路径 | 正常渲染 | `file://` | 与路径无关（H3/H4） | 需要逆向 |
| 内部路径 | 显示源码 | — | Gaia 其实也不行 | 接受限制 |

---

## 三、我需要你做的三件事（按顺序）

| # | 操作 | 耗时 |
| --- | --- | --- |
| **1** | 把 `/sdcard/Download/probe-3be36b2.apk` 导入 Gaia 并**启动一次** | 2 分钟 |
| **2** | 在 Gaia 的 Via 里打开 `file:///sdcard/Download/wvtest.html`，停在页面 | 1 分钟 |
| **3** | 在 Gaia 的 Via 里打开主页（点主页按钮），停在页面 | 30 秒 |

每一步做完告诉我，我立刻抓 logcat + 截图。

**我会做的**：
- 每步后 `logcat -d -b all`（不清缓冲、不操作 UI）
- 截图（`screencap`）
- 提取探针报告并解读

---

## 附：`wvtest.html` 的内容与判读

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>PROBE-WEBVIEW-OK</title>
<style>
  body { background: #0a3d62; color: #fff; padding: 40px; }   /* 深蓝背景 */
  h1   { color: #f6b93b; font-size: 40px; }                    /* 金黄标题 */
  .box { background: #e58e26; color: #000; padding: 20px; }    /* 橙色方块 */
  #js  { background: #38ada9; color: #fff; padding: 20px; }    /* 青色 JS 区 */
</style>
</head>
<body>
  <h1>WEBVIEW RENDER OK</h1>
  <div class="box">看到橙色方块 = HTML+CSS 渲染正常</div>
  <div id="js">JS: 未执行</div>
  <script>
    document.getElementById('js').textContent =
      'JS: 已执行  title=' + document.title + '  href=' + location.href;
  </script>
</body>
</html>
```

**一眼判读**：

| 看到的 | 说明 |
| --- | --- |
| 深蓝底 + 金黄色标题 + **橙色方块** | HTML + CSS 正常 |
| 青色区块里写 `JS: 已执行 title=PROBE-WEBVIEW-OK href=file:///sdcard/Download/wvtest.html` | JS 正常，且**确认是从 `file://` 加载的** |
| 青色区块仍是 `JS: 未执行` | JS 被禁（不影响"能否读文件"的结论） |
| 满屏 `<DOCTYPE html>...` 源码 | **读到了但被当纯文本 / 或渲染进程读不到后回退** |
