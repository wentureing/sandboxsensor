# 调查：WebView 是否被 patch，会不会暴露容器环境

- 日期：2026-11-22
- 相关：`webview-rootcause-20261122.md`（p17 修复）

---

## 一、结论先行

> **不 patch WebView = 给 guest 留了一条读真实文件系统的逃逸通道。**
> **patch WebView 后，WebView 的路径访问与宿主容器一致，暴露面反而更小。**

所以 p17 的修复方向（把 `libwebviewchromium*` 加入 patch 白名单）在**功能**和**安全**上是同向的，不存在取舍。

---

## 二、两种状态下的行为对比

| 维度 | 不 patch（p13–p16） | patch（p17+） |
| --- | --- | --- |
| WebView 读 `/data/user/0/<guest>/files/x.html` | 用**真实**路径 → 沙箱里没这个路径 → **失败** | 经容器翻译 → 命中沙箱文件 → **成功** |
| WebView 读 `/sdcard/Download/x` | 用**真实**路径 → **能读到宿主真实文件** | 被重定向到沙箱 `/sdcard/` → 读不到宿主文件 |
| 本地 HTML 渲染 | 加载失败 → 回退成纯文本（显示源码） | 正常渲染 |
| 长按文字选择 | 内部资源加载异常 → ANR | 正常 |
| WebView 沙箱进程的文件可见范围 | **宿主应用的全部存储权限范围** | 仅沙箱内 |

---

## 三、不 patch 时的具体暴露面（按可利用性排序）

### ① `file://` 直接读宿主文件（最高危）

WebView 默认 `allowFileAccess=true`（API 30+ 默认改为 `false`，但很多应用显式打开）。只要没被 patch：

```html
<!-- guest 里的网页（或 Via 的本地页） -->
<script>
fetch('file:///sdcard/DCIM/xxx.jpg')
  .then(r => r.blob())
  .then(b => exfiltrate(b));
</script>
```

**不 patch 时**：这个 `fetch` 会读到**宿主的真实文件**（只要应用有存储权限）。
**patch 后**：`file:///sdcard/...` 被重定向进沙箱 → 读不到宿主文件。

### ② WebView 沙箱进程的权限继承

WebView 渲染跑在 `com.google.android.webview:sandboxed_process0`（isolated uid）。它的文件访问**通过父进程的 fd 或路径**：

- 路径访问 → 走 `libwebviewchromium` 的 `open()` → 不 patch 时用真实路径
- **isolated uid 本身**不能读应用私有目录，但 `/sdcard` 在应用的存储权限范围内是可读的

所以不 patch 时，**guest 里的网页能读宿主的 `/sdcard`**。

### ③ 路径改写可被检测

guest 若能观察到"本地文件路径和实际不符"，就能推断自己在容器里：

```javascript
// 不 patch：file:// 加载失败 -> 说明该路径不存在 -> 但 Android 上它本该存在
// patch：加载成功（走沙箱） -> 行为与真机一致
```

### ④ 与缺陷 2 的耦合

不 patch 时，Via 主页加载失败后会回退 `loadData(html)`（MIME 默认 `text/plain`）→ **显示源码**。这本身就是一个"我在异常环境里"的强信号。

---

## 四、patch 后的副作用（需要观察的）

patch 大库（WebView 有几个 MB）理论上可能引入问题，类似 GPU 驱动那次。但成因不同：

| 对比项 | GPU 驱动（p13 的教训） | WebView（p17） |
| --- | --- | --- |
| patch 后崩的原因 | 驱动**内部**要求直接访问 `/vendor`、GPU 缓存、属性 | WebView **本来就该**通过 Android 的文件 API 访问，路径翻译是它预期的行为 |
| 风险等级 | **高**（已实测 abort） | **中低**（需观察） |
| 回退方式 | — | 白名单是精确匹配，可单独去掉 `libwebviewchromium` |

**关键区别**：GPU 驱动是"系统底层组件，假设自己独占文件系统"；WebView 是"应用层组件，本来就跑在应用沙箱里，路径被改写是它熟悉的场景（如多用户、work profile）"。

---

## 五、可执行的验证实验

### 实验 A：`file://` 能否读宿主文件（最直接）

用 Via（或任何 WebView 应用）在容器内访问：

```
file:///sdcard/Download/
```

| 环境 | 预期 |
| --- | --- |
| 真机（容器外） | 能列出 Download 目录 |
| **p16（无 patch）** | **能列出（暴露）** |
| **p18（有 patch）** | **不能列出（被重定向到沙箱内的空目录）** |

**判据**：如果 p16 能列出、p18 不能 → 证明 patch 确实收紧了暴露面。

### 实验 B：探针加 WebView 项（长期）

在探针里加一个 `WebViewTestActivity`：

```java
WebView wv = new WebView(this);
wv.getSettings().setAllowFileAccess(true);
wv.loadUrl("file:///sdcard/Download/");
wv.setWebViewClient(new WebViewClient() {
    @Override public void onPageFinished(WebView v, String url) {
        // 注入 JS 读 document.body.innerText，看能否列出文件
        v.evaluateJavascript("document.body.innerText", value -> {
            Log.i("PROBE", "WEBVIEW file:///sdcard -> " + value);
        });
    }
    @Override public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
        Log.i("PROBE", "WEBVIEW error: " + e.getDescription());
    }
});
```

**这一项同时能验证**：
1. WebView 是否被 patch（`file:///sdcard` 能否读）
2. 缺陷 2 的 MIME 问题（本地 HTML 是否正常渲染）
3. 缺陷 1 的长按 popup（WebView 内长按）

### 实验 C：对比 p16 / p18 的探针报告

如果探针以后加了 WebView 项，可以直接用 `probe-matrix.py` 对比两个版本的 `file://` 行为。

---

## 六、对"该不该 patch"的最终判断

| 角度 | 结论 |
| --- | --- |
| **功能** | 必须 patch（否则 Via 主页源码 + 长按 ANR） |
| **安全** | patch 更好（收窄 WebView 读宿主文件的能力） |
| **稳定性** | 需观察（但 WebView 与 GPU 驱动性质不同，风险中低） |
| **可回退** | 白名单精确匹配，出问题可单独去掉 |

**所以 p17 的修复没有"安全 vs 功能"的取舍** —— 这是少见的双赢修改。

---

## 七、一个仍需注意的残留暴露

即使 patch 了 WebView，**`file://` 协议本身仍可用**，只是被限制在沙箱内。如果 guest 里的网页需要读自己的文件，这是正常的；但如果想读宿主文件，patch 后就读不到了。

**真正需要 guest 感知的暴露**仍然是之前列的 P0 项（见 `hook-granularity-runtime-20261122.md`）：

- `/proc/self/maps`（`N10 pkg_hit=13`）
- `P13` 宿主包可见、`P16` 宿主 data 可 stat
- `P01` uid 暴露

WebView patch 不解决这些，但它是**必要的一步**——否则 guest 可以通过一条 `fetch('file:///sdcard/...')` 绕过所有其它检测。
