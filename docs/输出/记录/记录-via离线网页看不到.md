# 记录：via 主页源码 + 离线网页看不到（原基线自带，不修复）

- 日期：2026-11-25
- 用户明确：**这是原基线的一部分，暂不在重构中研究/修复，只记录**
- 设备：10AFA8169Z0039G / 容器 `baseline-v150`（254324b + 诊断层）

---

## 一、症状

| # | 症状 |
| --- | --- |
| 1 | via 主页加载出来是**HTML 源码**（不是渲染后的页面） |
| 2 | 在 via 里保存了**两个离线网页**，但**看不到**（疑似与 1 同源） |

---

## 二、离线网页：文件写成功了，但索引/路径不对

### 2.1 实际文件位置（都在，且完整）

```
/data/user/0/com.nextvm.app/files/virtual/data/mark.via_1795575262333/files/
  homepage2.html    6381 bytes   ← 完整 HTML（有 <script>、<link>、base64 图标）
  homepage.css      5661 bytes   ← 配套样式
```

`homepage2.html` 的 CSS 引用：
```html
<link rel="stylesheet"
      href="/data/user/0/com.nextvm.app/files/virtual/data/mark.via_.../files/homepage.css">
```
→ **指向沙箱真实路径** ✓

### 2.2 数据库里记的路径（错误的那一半）

`databases/via` 的 `tabs` 表：

| 列 | 值 |
| --- | --- |
| `url` | `https://www.baidu.com/` / `https://cn.bing.com/` |
| **`file_path`** | **`/storage/emulated/0/Android/data/com.nextvm.app/files/tabs/812d596d-...`** |

**问题**：`file_path` 是**宿主 external 路径**，而且用的是**容器包名 `com.nextvm.app`**，
不是 guest 包名 `mark.via`。

### 2.3 tab 文件确实写到了宿主目录

```
/storage/emulated/0/Android/data/com.nextvm.app/files/tabs/
  -rw-rw---- 812d596d-...  3072 bytes
  -rw-rw---- ad8a6488-...  2644 bytes
```

内容是序列化的 `Bundle`，解开后：
```
CUR  → URL = https://www.baidu.com/
LIST → Bundle { url = file:///data/user/0/com.nextvm.app/files/virtual/dat... }
```
→ **Bundle 里是沙箱路径（对），数据库 `file_path` 是宿主路径（错）**

### 2.4 索引表全空

| 表 | 行数 |
| --- | --- |
| `bookmarks` | **0** |
| `favorites` | **0** |
| `bookmark_items` | **0** |
| `bookmark_folders` | **0** |
| `downloader.tasks` | **0** |
| `history` | 2（就是那两个 URL） |
| `tabs` | 2 |

**所以"看不到"的直接原因**：via 的"离线/书签"列表来自 `bookmarks`/`favorites`
→ **全 0 行** → 列表自然空。

**但文件确实写了** → 说明"保存"这个动作在容器里是**半成功**：
落盘成功，但**登记到索引的那一步失败或走了别的路径**。

---

## 三、根因候选（都指向"路径解析不一致"）

| # | 候选 | 证据 |
| --- | --- | --- |
| **1** | **`getExternalFilesDir()` 拿到的不是 VirtualContext 的返回值** | `tabs.file_path` 用了宿主 external + 容器包名。而 `VirtualContext.getExternalFilesDir` 明明返回沙箱路径（`SandboxManager.getExternalAppFilesDir`） |
| 2 | 重定向规则不覆盖容器包名 | v142 规则只覆盖 `/storage/emulated/0/Android/data/$guestPackageName/`（`mark.via`），**不覆盖 `com.nextvm.app`** → 写到宿主不会被打回沙箱 |
| 3 | 索引写入失败（未记录原因） | `bookmarks=0`，但无异常日志（breadcrumb 里没有相关失败） |

**候选 1 最可疑**：via 某处拿到的 `Context` 是**宿主**的（不是 VirtualContext），
于是 `getExternalFilesDir()` 返回 `/storage/emulated/0/Android/data/com.nextvm.app/files`。

> **验证方法（待做）**：新构建的 guest 侧诊断已能把 `ctx getExternalFilesDir(...) -> ...`
> 写进 `checks.log`。下次跑 via 时看这一行到底返回宿主还是沙箱。

---

## 四、与「主页显示源码」的关系

用户判断"可能故障点一致"，从机制上**很可能同源**：

| 症状 | 共同点 |
| --- | --- |
| 主页显示源码 | WebView 拿到 `file://` 内容后**没按 HTML 渲染**（MIME/加载方式问题） |
| 离线网页看不到 | 首页 HTML 在 `files/` 里，但 via 的索引/路径指向宿主 external |

**两者都表现为"HTML 在沙箱里，但 via/WebView 从别处找"**。

**但这是原基线自带的问题**（`254324b` 就有），**不是重构引入**：
- 之前已记录的 W2（`text/plain`）就是主页源码的直接证据
- 本次新增的是"离线网页"这条独立证据

---

## 五、结论（只记录，不修复）

> `baseline-v150` 上，via 的**文件落盘正常**（HTML/CSS 都在沙箱、内容完整），
> 但 **`tabs.file_path` 记录了宿主 external 路径**，且 **`bookmarks`/`favorites` 索引为空** —
> 这两点解释了"保存了但看不到"。

**待验证（下次真机）**：`checks.log` 里的 `ctx getExternalFilesDir(...)` 返回值，
判断 via 拿到的是沙箱还是宿主路径。

**不修复原因**（用户明示）：这属于原基线自带缺陷，重构阶段只记录，避免把
"原项目的问题"和"重构引入的问题"混在一起。
