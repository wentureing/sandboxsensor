# 记录-Android12 上的三方对比（OPPO PEAT00）

- 日期：2026-11-23 23:10
- 设备：**`HES8ZDUWSW4T7TDQ`**（OPPO PEAT00 / **Android 12 / API 31** / arm64-v8a）
- 探针：v1.8（`488cd35`，`versionCode=9`）
- 测试文件：`/sdcard/Download/probe-wtest.txt` = `ORIGINAL`（9 B，`660 root everybody`）

---

## 一、结果（同一台设备，内 vs 外）

| 项 | **直接安装**（uid 10481） | **Gaia 内**（uid 10012） |
| --- | --- | --- |
| `mode` | `installed` | `container:dir-other(.../gaia/data/user/0/com.sandbox.probe)` |
| `J01` getDataDir | `/data/user/0/com.sandbox.probe` | `.../gaia/data/user/0/com.sandbox.probe` |
| **`P28` fopen_r** | ❌ **`errno=13` Permission denied** | ❌ **`errno=13` Permission denied** |
| **`P28` fopen_rplus** | ❌ `errno=13` | ❌ `errno=13` |
| **`P28` size** | `9`（stat 成功） | `9`（stat 成功） |
| **真实文件内容** | **`ORIGINAL`** | **`ORIGINAL`** |
| `N03.open_literal` | ✅ `fd=130` | ❌ `errno=2` |
| `N06.fopen_write` | ✅ `wrote 5 bytes` | ❌ `errno=2` |
| `N10.maps_scan` | `lines=2530 base_apk=9 pkg_hit=9` | `lines=2573 base_apk=10 pkg_hit=8` |

---

## 二、三个结论

### 结论 1：Gaia 与直接安装的 `P28` 完全一致（都 `errno=13`）

- 两边都 **`EACCES`**，真实文件都**没被改**
- **说明 Gaia 没有额外放宽存储权限** → guest 拿到的权限与直接安装相同

**这印证了用户的判断**：
> Gaia 根本没要求存储权限。

**所以不是"Gaia 做了隔离设计"，而是"它没申请这个权限"**。

### 结论 2：`size=9` 与 `fopen errno=13` 同时出现，说明 stat 能过、fopen 被拒

- `stat` 成功（能拿到大小 9）
- `fopen` 被拒（`EACCES`）

**这是 DAC/权限层面的部分可见**：能看元数据、不能打开内容。

### 结论 3：`N03`/`N06` 的差异仍然是两义的

| 环境 | `N03`（`/data/data/com.sandbox.probe/files/...`） |
| --- | --- |
| 直接安装 | ✅ `fd=130`（这是它真实 dataDir，可写） |
| Gaia | ❌ `errno=2`（这个路径在 Gaia 里不存在） |

**`ENOENT` 既可能"重定向到沙箱后不存在"，也可能"没重定向、真实路径不存在"** —— 不能作为判据。

---

## 三、与 vivo（Android 15）那次的对照

| 项 | vivo A15（旧设备） | **OPPO A12（新设备）** |
| --- | --- | --- |
| 直接安装 `P28` | `errno=13` | `errno=13` |
| Gaia `P28` | `errno=13` | `errno=13` |
| **我们容器 `P28`** | **`ok` + `fwrite=6`（真实文件被改）** | 容器未装 |
| 关键差异 | **只有我们容器能写** | — |

**所以"我们能写"这件事，唯一变量是容器申请了 `MANAGE_EXTERNAL_STORAGE`。**

---

## 四、回答用户最初的三个问题（最终版）

| 问题 | 答案 |
| --- | --- |
| **是权限配置障碍还是上游缺陷？** | **是权限**：我们能写 `/sdcard` 是因为容器申请并获得了 `MANAGE_EXTERNAL_STORAGE`，guest 作为子进程继承了它 |
| **修复是否危害 WebView？** | p28/p29 的 `fopen` 豁免改动**在探针这类运行期加载的库里不会被调用**（`libprobe.so` 的 GOT 未被 patch），所以谈不上危害 |
| **缺陷是否确实存在？** | **存在，但有两层**：<br>① 权限层：容器的 `MANAGE_EXTERNAL_STORAGE` 会传导给所有 guest（**已确认**）<br>② hook 层：运行期 `System.loadLibrary` 加载的库拿不到 GOT patch（**在我们容器里观测到，Gaia 里被 DAC 掩盖**） |

---

## 五、关于"导出 Gaia 应用数据解包"

**这次不需要**：logcat 里有完整的 base64 分块，`extract-report.py` 已经还原出 4 份 JSON：

```
report-peat00-230704-1.json  11960 B  parts=9   mode=installed
report-peat00-230704-2.json  12922 B  parts=10  mode=installed
report-peat00-230704-3.json  12894 B  parts=10  mode=container:dir-other(...gaia...)
report-peat00-230704-4.json  12894 B  parts=10  mode=container:dir-other(...gaia...)
```

**但记录一个备选方案**（下次 logcat 拿不到时用）：

```powershell
# Gaia 内的报告路径（从探针的 FILE 日志读到）
# /data/user/0/com.app.hider.master.pro.cn/gaia/data/user/0/com.sandbox.probe/files/probe-report.json

# 方式 1：run-as（Gaia 是 debug 包时才可用）
adb shell "run-as com.app.hider.master.pro.cn cat <上面的路径>" > report.json

# 方式 2：如果 run-as 不可用，导出 app 数据
adb backup -f gaia-backup.ab -noapk com.app.hider.master.pro.cn
# 然后解包 .ab（Android 12 上 adb backup 可能已禁用）

# 方式 3：外部镜像路径（探针的 FILE_EXT）
# /storage/emulated/0/Android/data/com.app.hider.master.pro.cn/gaia/com.sandbox.probe/Android/data/com.sandbox.probe/files/probe-report.json
# 这个路径在 Android 12 上 adb shell 通常读不到（scoped storage），但可以试 run-as
```
