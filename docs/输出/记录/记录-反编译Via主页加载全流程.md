# 记录：反编译 Via 7.3.3 主页加载全流程（DEX method 级）

- 日期：2026-11-24 19:25（device）
- 来源：`dist/via-7.3.3.apk` → `classes.dex`（5,197,292 B，单 dex）
- 方法：自写的 DEX 解析器（header → string_ids/type_ids/method_ids/class_defs → class_data
  → code_item，扫 `const-string` 和 `invoke-*`）
- 统计：8047 classes / 38160 methods 有 code，命中 15 个方法

---

## 一、Via 的主页加载流程（完整还原）

```
Via 启动
  │
  ├─① 生成两个文件，写到 getFilesDir()
  │     homepage2.html（内含 <img src="file:///android_asset/logo.svg">）
  │     homepage.css
  │     （代码位置：e9.u.onWriteFinished / r8.d.onWriteFinished / u9.d）
  │
  ├─② loadUrl("file://" + getFilesDir() + "/homepage2.html")
  │
  ├─③ WebView 对每个资源调 shouldInterceptRequest（Via 的实现）
  │     url 以 "homepage.css" 结尾 → Via 自己读文件 + 返回 text/css
  │     其他（homepage2.html 本身、logo.svg）→ 返回 null → 交给 WebView
  │
  ├─④ WebView 加载 homepage2.html（file://）
  │     容器外：判 text/html → 正常渲染 ✅
  │     容器内：判 text/plain → 显示源码 ❌   ← Via 主页的问题点 1
  │
  └─⑤ HTML 里的 <img src="file:///android_asset/logo.svg">
        容器外：AndroidProtocolHandler 打开 → 正常 ✅
        容器内：AndroidProtocolHandler 打不开 → logo 缺失 ❌  ← 问题点 2
```

---

## 二、三个关键方法的原始证据

### 2.1 `d9.q$c.shouldInterceptRequest`（Via 的 WebViewClient 实现）

```
CALL p4.d.a
CALL android.net.Uri.toString
"homepage.css"
CALL java.lang.String.endsWith
CALL d9.q.r3
```

**还原成 Java**：
```java
@Override
public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest req) {
    if (req.getUrl().toString().endsWith("homepage.css")) {
        return q.this.r3(...);       // 只处理 css
    }
    return null;                      // 其他全部交给 WebView
}
```

**关键**：**Via 只拦截 `homepage.css`，`homepage2.html` 本身和 `logo.svg` 都不拦。**

### 2.2 `d9.q.E`（就是上文的 `r3`）

```
"text/css"
CALL ac.e.g
CALL androidx.fragment.app.Fragment.I
CALL android.content.Context.getFilesDir
"homepage.css"
CALL java.io.File.<init>
CALL java.io.File.getAbsolutePath
CALL java.io.FileInputStream.<init>
CALL java.io.BufferedInputStream.<init>
CALL android.webkit.WebResourceResponse.<init>
CALL java.util.HashMap.<init>
"Cache-Control", "no-cache", put
"Access-Control-Allow-Origin", "*", put
"Content-Type", put
CALL c8.g8.a
CALL java.lang.Throwable.printStackTrace
```

**还原成 Java**：
```java
WebResourceResponse r3(String url) {
    try {
        File f = new File(context.getFilesDir(), "homepage.css");
        FileInputStream fis = new FileInputStream(f.getAbsolutePath());
        BufferedInputStream bis = new BufferedInputStream(fis);
        HashMap<String, String> headers = new HashMap<>();
        headers.put("Cache-Control", "no-cache");
        headers.put("Access-Control-Allow-Origin", "*");
        headers.put("Content-Type", "text/css");
        return new WebResourceResponse("text/css", "utf-8", 200, "OK", headers, bis);
    } catch (Throwable t) { t.printStackTrace(); return null; }
}
```

**关键**：**Via 对 `homepage.css` 自己读文件 + 手动给 `text/css`** →
**这条路在容器内是正常的**（因为 `FileInputStream` 不被容器 hook，`getFilesDir()` 返回真实沙箱路径）。

### 2.3 `e9.u.onWriteFinished`（生成 HTML）

```
CALL java.lang.Object.getClass
"<img class=\"smaller\" src=\"file:///android_asset/logo.svg\" />"
CALL java.lang.String.contains
CALL w9.l.x0 / w9.l.k / e9.k.Y2 / e9.s.<init>
CALL z8.h.b
...
CALL d9.t.p
```

**关键**：**Via 生成的 HTML 里硬编码了 `file:///android_asset/logo.svg`。**

---

## 三、这解释了一个之前没想通的细节

**Via 主页显示源码时，样式看起来是对的** —— 因为：

| 资源 | 谁处理 | 容器内结果 |
| --- | --- | --- |
| **`homepage.css`** | **Via 自己**（`shouldInterceptRequest` → 手动给 `text/css`） | ✅ 正常 |
| **`homepage2.html`** | **WebView 自己**（`file://`） | ❌ 显示源码 |
| **`logo.svg`** | **WebView 自己**（`file:///android_asset/`） | ❌ 打不开 |

**所以 Via 主页的"两处故障"是分开的，都指向同一个根因域：WebView 自己处理 `file://` 时的行为。**

---

## 四、对方案 A 的决定性影响

### 4.1 方案 A 与 Via 的 `shouldInterceptRequest` **零冲突**

Via 的实现只处理 `homepage.css`，**其他都返回 null**。容器的包装层：

```java
// 容器包装后的 client
public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest req) {
    // 1) 先问 Via 的 client
    WebResourceResponse viaResp = viaClient.shouldInterceptRequest(v, req);
    if (viaResp != null) return viaResp;          // ← Via 处理 homepage.css，容器不干扰

    // 2) Via 返回 null 的（homepage2.html 本身、logo.svg 等）→ 容器接管
    Uri u = req.getUrl();
    if ("file".equals(u.getScheme())) {
        // 2a) file:///android_asset/xxx → 读 guest 的 assets
        // 2b) file://<真实路径> → 读文件
        // 两路都按扩展名给正确 MIME
        return new WebResourceResponse(mime, "utf-8", stream);
    }
    return null;                                   // 其他协议不碰
}
```

**三条保证**：
1. **Via 的 css 逻辑完全不受影响**（先问它，它返回非 null 就尊重）
2. **`homepage2.html` 本身被接管** → MIME 正确 → 正常渲染
3. **`logo.svg` 被接管** → 容器自己读 assets → 图片显示

### 4.2 被拦截的请求数在可接受范围

**Via 主页只有 2 类资源走容器接管**：
- `homepage2.html`（1 次）
- `logo.svg`（1 次）

**→ 每次主页加载只多 2 次 Java 层拦截，性能影响可忽略。**

---

## 五、性能代价讨论（对比 open() hook）

### 5.1 两者的调用频次量级完全不同

| 层 | hook 点 | 触发频率 | 单次代价 | 量级 |
| --- | --- | --- | --- | --- |
| **native** | `open()` / `openat()` | **每次文件操作**（一个应用启动可能几千次，含 dex/资源/配置） | `strncmp` 前缀匹配 + 可能的 `std::string` 拼接 | **纳秒 ~ 微秒** |
| **Java** | `shouldInterceptRequest` | **WebView 每次资源请求**（一个页面几十到几百次） | **JNI 跨界 + Java 对象分配 + 读文件 + 建 stream** | **十微秒 ~ 毫秒** |

### 5.2 但总量上 open() 更贵

| | `open()` hook | `shouldInterceptRequest` hook |
| --- | --- | --- |
| **单次代价** | 低（纳秒~微秒） | 高（十微秒~毫秒） |
| **调用次数** | **极高**（一个进程生命周期内几万次） | **低**（只在 WebView 加载时，几十~几百次） |
| **累计开销** | **几毫秒 ~ 几十毫秒**（分布在全生命周期） | **1 ~ 50 毫秒**（集中在加载瞬间） |
| **能否省掉** | 不能（每次 open 都要判） | **能** —— 只对 `file://` 生效，其他协议直接返回 null |

### 5.3 关键：两个 hook 的代价性质不同

| 维度 | `open()` 重定向 | `shouldInterceptRequest` 补 MIME |
| --- | --- | --- |
| **是否热路径** | ✅ 是（每次 IO） | ❌ 否（只在 WebView 加载） |
| **是否可短路** | 部分（`g_path_redirects.empty()` 直接返回） | ✅ **完全可短路**（非 `file://` 立即 return null） |
| **是否跨语言** | ❌ 纯 native，无 JNI | ✅ 有 JNI 跨界 + 对象分配 |
| **是否分配内存** | 只在匹配时分配 `std::string` | 每次命中分配 `WebResourceResponse` + `FileInputStream` |
| **对 UI 线程的影响** | 无（在调用线程） | **`shouldInterceptRequest` 在 IO 线程池，不在主线程** |

### 5.4 量化估算

**Via 主页加载（实测资源数很少）**：
- `homepage2.html`（1 次拦截）
- `logo.svg`（1 次拦截）
- `homepage.css`（Via 自己处理，容器不碰）
- **容器接管 2 次** → 每次约 100 μs（读 74 B 文件 + 建 stream）→ **总共约 0.2 ms**

**对比：一次 `open()` hook 约 50-200 ns，但一个进程启动有几万次 → 累计几毫秒。**

**结论**：
> **`shouldInterceptRequest` 补 MIME 的总开销（0.2 ms/主页）远低于 `open()` 重定向的全生命周期开销（几 ms）。**
> **而且它天然可短路（非 file:// 直接过），不需要额外优化。**

### 5.5 唯一需要注意的性能点

**如果应用是"资源密集型网页应用"**（一个页面 500+ 子资源）：
- 500 次 × 100 μs = **50 ms**
- 这在首屏加载时可感知

**缓解**：
1. **只接管"WebView 自己会判错 MIME"的扩展名**（`.html` / `.htm` / `.svg` / `.css` / `.js` / `.json` / `.png` 等）
2. **`.js` / `.css` 这类如果 Via 已经自己处理了就不重复**
3. **如果扩展名不在白名单 → 直接返回 null**（让 WebView 自己走，代价为零）

**这样实际接管的请求数会非常少。**

---

## 六、结论

| 问题 | 答案 |
| --- | --- |
| **Via 的主页怎么加载** | `loadUrl("file://" + getFilesDir() + "/homepage2.html")`，HTML 内含 `file:///android_asset/logo.svg` |
| **Via 的 `shouldInterceptRequest` 干什么** | **只处理 `homepage.css`**（自己读文件 + 手动 `text/css`），其余返回 null |
| **方案 A 会破坏 Via 吗** | **不会** —— 先问 Via，它返回非 null 就尊重 |
| **方案 A 能修好哪些** | `homepage2.html`（MIME）+ `logo.svg`（assets 读取） |
| **性能代价** | **主页约 0.2 ms**；即使 500 资源也只需 50 ms，且可通过扩展名白名单进一步压低 |
| **对比 open() hook** | **`shouldInterceptRequest` 总开销更小**（调用频次低 2-3 个数量级） |
