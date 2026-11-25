# 记录：via 保存离线网页 —— 干净环境复现 + 完整证据链

- 日期：2026-11-25 11:07–11:09
- 环境：**干净安装**（`pm uninstall` → install `758fda6`，versionCode 78，appops 已授权）
- 实例：`mark.via_1795576024771`（全新）
- 用户操作：在 via 里**保存了两个离线网页**
- 结论：**保存动作完全静默失败 —— 连"尝试写文件"都没发生**

> ⚠️ **结论已修正（2026-11-25 反编译后）**：保存其实**成功**了，三个完整 MHTML 在沙箱里。
> 真实缺陷是「**写沙箱、读宿主**」的路径根不一致。请以 记录-via离线保存路径不一致.md 为准。
> 本文保留作为排查过程的记录。

---

## 一、时间线（logcat + breadcrumb 对齐）

| 时刻 | 事件 | 来源 |
| --- | --- | --- |
| 11:07:58 | via 启动完成，`cb onActivityResumed mark.via.Shell` | BootCrumb |
| 11:08:08 | `Failed to create temp profile file for /data/misc/profiles/cur/0/mark.via/primary.prof` | logcat（ART，无关） |
| 11:08:38 | `via.db` / `downloader.db` 落盘（此时 tabs/bookmarks 仍 0） | mtime |
| 11:08:39–11:08:58 | 我这边在 cat 文件（不影响） | adbd |
| **11:09:01.379** | **`WindowOnBackDispatcher: sendCancelIfRunning: callback=Dialog$$...`** | logcat |
| **11:09:01.451** | **`BootTrace: >> registerReceiverWithFeature str=android.widget.AbsListView$7`** | logcat |
| 11:09:01.981 | 第二个 `Dialog` 回调 | logcat |
| 11:09:03.678 | `v124-login-probe total=37 changed=3`（View 树变了） | BootCrumb |

→ **用户确实打开了保存对话框**（Dialog + AbsListView），UI 有响应。

---

## 二、结果：**零痕迹**

### 2.1 数据库全空

| 表 | 行数 |
| --- | --- |
| `tabs` | **0** |
| `bookmarks` | **0** |
| `favorites` | **0** |
| `bookmark_items` / `bookmark_folders` | **0** |
| `downloader.tasks` | **0** |
| `history` | 2（`cn.bing.com` / `baidu.com`，仅访问记录） |

### 2.2 文件系统：没有"保存的网页"

`<instance>/` 下 11:07 之后新增的文件：

```
files/icon/www.baidu.com.png          ← 站点图标（访问过）
cache/webview_mark.via/Default/HTTP Cache/Code Cache/js/*   ← 大量 JS 缓存（页面渲染过）
```

**但没有**：
- 新的 `.html` / `.mht` / `.webarchive`
- `sdcard/Download/` 空
- `external_files/Download/` **不存在**

### 2.3 宿主 external 的 `tabs/` 也空

```
drwxrws--- 2 u0_a360 ext_data_rw /storage/emulated/0/Android/data/com.nextvm.app/files/tabs
```
**目录存在、属主正确（当前 uid 360）、可写 —— 但里面是空的。**

> 对比：**上一次**（旧实例、非干净安装）这个目录里有 2 个序列化 Bundle（3072/2644 B）。
> 说明"写宿主"这件事**有时会发生**，这次**完全没发生**。

### 2.4 无异常、无阻塞

- `stackdump-403.log`（149 KB）：所有线程 `RUNNABLE` / `TIMED_WAITING`，**无 BLOCKED、无死锁**
- `breadcrumb-403.log`：无任何与保存/IO 相关的失败记录
- via 自己的 `Log` 输出：**被 vivo 吞掉**（只有 `m.nextvm.app:p0` 前缀的容器日志可见）

---

## 三、结论

> **保存动作在 UI 层之后、落盘之前就静默终止了。**

具体表现：
1. 对话框打开了（有 Dialog/AbsListView 痕迹）
2. **没有调用 `DownloadManager`**（`downloader.tasks=0`）
3. **没有写任何文件**（三个候选目录全空）
4. **没有写数据库**（tabs/bookmarks 全 0）
5. **没有任何错误日志**

**这是最坏的失败形态**：无声无息，用户只能看到"点了没反应/列表里没有"。

---

## 四、缺陷猜想（按可能性排序，均待验证）

| # | 猜想 | 依据 | 如何验证 |
| --- | --- | --- | --- |
| **1** | via 先**探测保存位置可用性**，探测失败就静默返回 | 目录可写却完全没写 | 需要 via 源码或 hook 其探测调用 |
| **2** | 探测用的路径是**宿主**的（`/storage/emulated/0`），与实际写入路径（沙箱）不一致 | 上上次出现过"写宿主"的现象 | `getExternalFilesDir` 返回值（当前拿不到，guest 侧无 `debug-trace.log`） |
| **3** | 保存功能依赖某个容器不支持的 API（如 `MediaStore` 插入、`SAF`） | `downloader.tasks=0` 且无文件 | hook `ContentResolver.insert` 观察 |
| 4 | 与"主页显示源码"同源（WebView 加载/序列化链路） | 两者都是"内容在沙箱但 via 拿不到" | 需先解决 W2（MIME） |

---

## 五、与「主页显示源码」的关系

| 症状 | 位置 | 共同形态 |
| --- | --- | --- |
| 主页显示源码 | WebView 渲染 | HTML 在沙箱，但**没按 HTML 渲染** |
| 离线网页看不到 | via 保存/读取 | 文件应写到沙箱，但**没写/写错地方** |

**共同点**：**"内容在沙箱 vs via/WebView 期望的位置"不一致**。

但两者是**独立缺陷**：
- 主页源码是 **MIME/加载方式**（W2 已记录 `text/plain`）
- 离线保存是 **写入路径/可用性探测**

---

## 六、当前无法进一步定位的原因

| 缺什么 | 影响 |
| --- | --- |
| via 自己的 `Log`（被 vivo 吞） | 看不到它内部报错 |
| guest 侧 `debug-trace.log` | 看不到 `getExternalFilesDir` 等返回值（`baseline-v150` 只在主进程装了 FileSink） |
| via 的保存实现 | 无法确认它判断"能否保存"的依据 |

**下一步（若将来要修）**：
1. 让 guest 进程也写 `debug-trace.log`（当前只写 `checks.log`）
2. 或在面包屑里加 `getExternalFilesDir` 的返回值记录
3. 或用 `strace`/`frida` 观察 via 的 `open` 调用（真机 root 场景）

---

## 七、结论一句话

> **干净环境下 via 的"保存离线网页"是彻底的静默失败：对话框开了、UI 有反应，
> 但文件、数据库、宿主目录三方全无痕迹，也没有任何错误日志。**
>
> 这属于**原基线自带缺陷**（`254324b` 就有），按用户要求**只记录不修复**。
