# 记录-Gaia 机制分析

- 更新：2026-11-23
- 合并自：`apphider-gaia-analysis`、`gaia-vs-ours`、`gaia-actual-paths`、`gaia-blackbox-test-plan`
- 对象：应用隐藏大师 `com.app.hider.master.pro.cn`（Gaia）

---

## 一、沙箱路径：**内部存储 + 路径镜像**（实测，推翻早期猜测）

### 1.1 决定性数据（探针 v1.7 在 Gaia 内运行）

| 探针 | **Gaia 内** | 直接安装（对照） |
| --- | --- | --- |
| `mode` | `container:dir-other(...)` | `installed` |
| **`J01` getDataDir** | **`/data/user/0/com.app.hider.master.pro.cn/gaia/data/user/0/com.sandbox.probe`** | `/data/user/0/com.sandbox.probe` |
| **`J16` nativeLibraryDir** | `.../gaia/data/app/com.sandbox.probe/lib/arm64` | `/data/app/~~96L…/lib/arm64` |
| **`J18` deviceProtected** | `/data/user_de/0/com.app.hider.master.pro.cn/gaia/data/user_de/0/com.sandbox.probe` | `/data/user_de/0/com.sandbox.probe` |
| `J19` uid | `10319` | `10320` |
| **`P05` maps hostHits** | **`lines=2868 hostHits=0`** | `lines=3034 hostHits=0` |

**结论：Gaia 的沙箱在 `/data/user/0/<容器>/gaia/`（内部存储），不是 `/sdcard`。**

> 早期我从目录名 `/sdcard/Android/data/<容器>/gaia/...` 推断"沙箱在外部存储"，
> 并据此写了"沙箱迁外部"的方案。**探针一跑就推翻了。**
> 教训：目录名不能当证据，必须实测路径。

### 1.2 Gaia 真正做的两件事

**① 路径镜像：保留完整的 `/data/user/0/<guest_pkg>` 结构**

```
Gaia:  /data/user/0/<容器>/gaia/data/user/0/<guest_pkg>
                            └──────────────────────┘
                            原样镜像了 guest 的"官方路径"

我们:  /data/user/0/<容器>/files/virtual/data/<guest_pkg>_<时间戳>
                                                  └─ instanceId 带时间戳，一眼可辨
```

**guest 看到的 `dataDir` 结尾和真机一模一样**，只是前面多了一层 `<容器>/gaia/data`。

**② `/proc/self/maps` 过滤**

Gaia 的真实路径含 `com.app.hider.master.pro.cn`，但 `P05 hostHits=0`
→ **Gaia 对 maps 做了过滤/伪造**。

对比我们：`N10 pkg_hit=13`（native 侧暴露）。**这一项 Gaia 明显做得比我们好。**

---

## 二、Gaia 做到的、我们没做到的

| 能力 | Gaia | 我们 | 备注 |
| --- | --- | --- | --- |
| **Via 主页正常渲染** | ✅ | ❌ 显示源码 | 见 `记录-WebView问题全链路.md` |
| **Via 里长按有菜单** | ✅（用户实测） | ❌ ANR | |
| **Via 打开 `/sdcard/Download/`** | ❌ 打不开 | ❌ 打不开 | **两者一致**：guest 的 `/sdcard` 都是沙箱内的 |
| maps 无宿主痕迹 | ✅ `hostHits=0` | ❌ `pkg_hit=13` | |
| 进程名用 guest 包名 | ✅ `ps` 显示 `com.sandbox.probe` | ❌ 显示容器包名 | |
| 路径含 guest 包名 | ✅ | ❌ 带时间戳后缀 | |
| 隐藏宿主包可见性 | ✅ `P13 hidden` | ❌ `P13 visible` | |
| 隐藏宿主 dataDir 可 stat | ✅ `P16 exists=false` | ❌ `P16 exists=true canRead=true` | |

**所以 Gaia 在"检测对抗"上整体强于我们，但"guest 的 `/sdcard` 是沙箱内的"这一点两家一致** ——
说明这是这类容器的共同设计，不是我们的缺陷。

---

## 三、Gaia 让 Via 主页正常的机制：推断为宿主代读

### 3.1 推理链

```
事实 1：Gaia 沙箱在内部存储（/data/user/0/<容器>/gaia/）
事实 2：WebView renderer 是 isolated uid，读不到别的应用的内部目录
事实 3：但 Via 主页在 Gaia 里正常渲染
=> 只能是：宿主（有权限）代读，用 WebResourceResponse 把 stream 交给 renderer
```

**同一原理的旁证**：容器 UI 的"browse APK"能导入，而命令行 `INSTALL --es path` 不能 ——
因为 SAF 返回 **fd**，fd 跨进程传递不受 isolated uid 限制。

### 3.2 尚未验证的部分

**没有直接证据**证明 Gaia 用了 `shouldInterceptRequest`。可能的替代实现：
- 替换 guest 的 `WebViewClient`（但 framework 类，很难）
- 在某些版本上 WebView renderer 可能继承了不同的 SELinux 域

**验证方案**（探针 v1.8）：把最小 HTML 写进探针自己的 `getFilesDir()`（落在 Gaia 沙箱内），
输出绝对路径，然后在 Gaia 的 Via 里打开：

| 结果 | 结论 |
| --- | --- |
| **正常渲染** | renderer 能读内部存储 → **Gaia 不需要 hook**，我们要重新找原因 |
| **显示源码** | renderer 读不到 → **证实是宿主代读** |

---

## 四、黑盒测试方法论（可复用）

### 4.1 这台设备连 adb 都读不了 Gaia 沙箱

```
$ adb shell ls /sdcard/Android/data/com.app.hider.master.pro.cn/gaia/mark.via
Android                                  ← 能列

$ adb shell ls .../gaia/mark.via/Android
ls: Permission denied                    ← 被拒（/storage/emulated/0 路径同样被拒）
```

**所以观察容器内部状态，唯一可靠的手段是「在容器内运行的探针」**，
不是 adb 文件系统访问，也不是 logcat 反推。

### 4.2 自动化流程（`tools/device/gaia-test.ps1`）

```
启动 Gaia → 按文本找探针图标 → 点「授予」→ 等进程 → 抓 logcat → 提取报告
```

关键设计（踩坑后的写法）：
- 坐标靠 `uiautomator dump` **动态查询**，不硬编码
- 轮询等元素，不用固定 sleep
- 权限窗口文案多变 → 候选列表 `授予/允许/同意/确定/始终允许`
- 失败时打印当前 UI 可见文本，便于当场定位

### 4.3 探针在 Gaia 内的输出落点（实测）

```
FILE     /data/user/0/com.app.hider.../gaia/data/user/0/com.sandbox.probe/files/probe-report.json
FILE_EXT /storage/emulated/0/Android/data/com.app.hider.../gaia/com.sandbox.probe/Android/data/com.sandbox.probe…
```

**`FILE_EXT` 也落在沙箱镜像里** → adb 拉不到。
→ 因此需要 `COLLECT_OUT`/`CAT_OUT` 这套导出机制（见 `计划/README.md` P0-2）。

---

## 五、可借鉴、但代价明确的三点

| Gaia 的做法 | 我们是否该抄 | 代价 |
| --- | --- | --- |
| **路径镜像**（`/data/user/0/<guest_pkg>` 原样保留） | ✅ 值得，**独立于 WebView** | 需要改 `instanceId` 的目录布局，涉及迁移 |
| **maps 过滤** | ✅ 值得，且是检测对抗的核心 | 需要改 native 快照逻辑 |
| **宿主代读 WebView 的 `file:/`** | ⚠️ 唯一可能的 WebView 方案 | 找不到注入点就白做；见 P1-3 |
