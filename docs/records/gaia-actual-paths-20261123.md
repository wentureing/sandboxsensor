# 实测结果：Gaia 的沙箱在「内部存储」，H1 假设被推翻

- 日期：2026-11-23
- 设备：`10AFA8169Z0039G`（vivo A15）
- 探针：v1.7（`versionCode=8`，`UiTestActivity` 兼作 ProbeRunner 入口）
- 数据：`probe-reports/report-gaia-run-170309-2.json`（Gaia 内）、`report-selfinstall-170228-2.json`（直接安装）

---

## 一、决定性数据：Gaia 的沙箱路径

| 探针 | **Gaia（容器内）** | 直接安装 |
| --- | --- | --- |
| `mode` | `container:dir-other(/data/user/0/com.app.hider.master.pro.cn/gaia/data/user/0/com.sandbox.probe)` | `installed` |
| **`J01` getDataDir** | **`/data/user/0/com.app.hider.master.pro.cn/gaia/data/user/0/com.sandbox.probe`** | `/data/user/0/com.sandbox.probe` |
| **`J16` nativeLibraryDir** | **`/data/user/0/com.app.hider.master.pro.cn/gaia/data/app/com.sandbox.probe/lib/arm64`** | `/data/app/~~96L.../lib/arm64` |
| **`J18` deviceProtected** | **`/data/user_de/0/com.app.hider.master.pro.cn/gaia/data/user_de/0/com.sandbox.probe`** | `/data/user_de/0/com.sandbox.probe` |
| `J19` uid | `10319` | `10320` |
| `P09` findLibrary | `.../gaia/data/app/com.sandbox.probe/base.apk!/lib/arm64-v8a/libprobe.so` | `.../base.apk!/lib/arm64-v8a/libprobe.so` |
| **`P05` maps hostHits** | **`lines=2868 hostHits=0 sandboxHits=0`** | `lines=3034 hostHits=0` |

**结论：Gaia 的沙箱在 `/data/user/0/<容器>/gaia/`（内部存储），不是 `/sdcard`。**

→ **`H1（沙箱在外部存储）被推翻。**

---

## 二、Gaia 真正做的两件事（数据支持）

### ① 路径镜像：保留完整的 `/data/user/0/<guest_pkg>` 结构

```
Gaia:  /data/user/0/<容器>/gaia/data/user/0/<guest_pkg>
                          └────────────────────────────┘
                          原样镜像了 guest 的"官方路径"

我们:  /data/user/0/<容器>/files/virtual/data/<guest_pkg>_<时间戳>
                                              └──── instanceId，不是官方路径 ────┘
```

**这是 Gaia 比我们强的地方**：guest 看到的 `dataDir` 结尾是 `/data/user/0/com.sandbox.probe`（**和真机一模一样**），只是前面多了一层 `<容器>/gaia/data`。

**而我们的 instanceId 带时间戳后缀**，一眼就能看出不是真机。

### ② `maps` 里没有宿主包名（`hostHits=0`）

**但** Gaia 的真实路径含 `com.app.hider.master.pro.cn`，`P05` 却报 `hostHits=0`。

→ **Gaia 对 `/proc/self/maps` 做了过滤/伪造。**

对比我们：`N10 pkg_hit=13`（native 侧暴露）。**这一项 Gaia 明显做得比我们好。**

---

## 三、关键矛盾：沙箱在内部，但 Via 主页正常

| 事实 | 含义 |
| --- | --- |
| Gaia 沙箱在 `/data/user/0/<容器>/gaia/` | WebView renderer（isolated uid）**理论上读不到** |
| 但 Via 主页**正常渲染** | **Gaia 一定在 WebView 层做了处理（H3）** |
| Via 打不开 `/sdcard/Download/wvtest.html` | renderer 也没有公共目录权限 |

**所以 Gaia 的机制几乎可以确定是**：

```
Via 用 file:// 请求
  → Gaia 在 guest 进程里拦截（WebViewClient.shouldInterceptRequest 或等价物）
    → 宿主（有权限）按镜像规则打开真实文件
      → 用 WebResourceResponse 把 stream 交给 WebView
```

**这与之前"UI 能导入、命令行不能"的原理完全一致**：**fd/stream 跨进程传递，不受 isolated uid 限制**。

---

## 四、对方案的直接影响

| 原方案 | 新结论 |
| --- | --- |
| **沙箱迁到外部存储**（1 行改动） | ❌ **不能解决问题**。Gaia 在内部做到了，说明问题不在路径位置 |
| **WebView 层 hook**（H3） | ✅ **唯一与证据相符的方向** |
| 路径镜像（含 guest 包名） | ✅ **有价值**，但解决的是"检测对抗"而非 WebView |

**所以"1 行改动"的方案作废。** 这条结论必须记录，避免以后有人照抄。

---

## 五、下一步验证（关键，成本低）

要区分「Gaia 用 hook」还是「renderer 其实能读内部」，需要一个**决定性实验**：

### 实验：让探针把 HTML 写进自己的 filesDir，再让 Via 打开

**为什么能区分**：

```
探针把自己的 filesDir 写一个 wvtest.html
  → 文件落在 Gaia 沙箱内：/data/user/0/<容器>/gaia/data/user/0/com.sandbox.probe/files/wvtest.html
    → Via 打开 file:///data/user/0/<容器>/gaia/data/user/0/com.sandbox.probe/files/wvtest.html
      ├─ 正常渲染 → renderer 能读内部存储（意外结论，说明 isolated uid 有权限）
      └─ 显示源码/失败 → renderer 读不到 → 证实 Gaia 用 WebView hook
```

**实现**：探针 v1.8 加一项 `P28`：
1. 把一段最小 HTML 写到 `getFilesDir()/wvtest.html`
2. 把绝对路径打到 logcat（`WEBVIEW_PROBE_PATH=<path>`）
3. 我读出路径，在 Via 里打开

**成本**：探针改动约 20 行 + 一次构建。

### 另一个更快的旁证

如果 Via 主页的地址栏是 `file://` 且指向 Gaia 沙箱路径，就已经证明「Via 用 file:// 加载、且成功了」。

**所以最简单的一步**：让用户在 Gaia 的 Via 里点主页，我截图读地址栏。

---

## 六、这次实测纠正的三个误判

| 误判 | 纠正 |
| --- | --- |
| "Gaia 沙箱在 `/sdcard`（外部）" | ❌ 实际在 `/data/user/0/<容器>/gaia/`（内部） |
| "把沙箱迁外部就能解决 WebView" | ❌ Gaia 在内部却做到了 → 路径位置不是原因 |
| "Gaia 只是路径不同" | ❌ 它还做了 maps 过滤（`hostHits=0`）和路径镜像（含 guest 包名） |

**教训**：从"目录名看起来像 `/sdcard/Android/data/...`"推断"沙箱在外部"，没有做验证就写进了方案。这次靠探针拿到了真实路径。
