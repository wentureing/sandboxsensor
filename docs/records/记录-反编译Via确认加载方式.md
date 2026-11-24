# 记录：反编译 Via 7.3.3 —— 它怎么加载 homepage2.html

- 日期：2026-11-24 19:20（device）
- 来源：`dist/via-7.3.3.apk`（2.51 MB，从设备 `/data/app/.../base.apk` pull）
- 方法：Python 读 APK 结构 + 提取 `classes.dex` 的字符串表（27745 条字符串）
- 目的：确认方案 A（hook `shouldInterceptRequest`）对 Via 是否有效

---

## 一、APK 结构

```
APK 条目数: 364
classes.dex: 5,197,292 B（单 dex）

assets/ 下只有 5 个文件：
  assets/dexopt/baseline.prof        1080 B
  assets/dexopt/baseline.profm        163 B
  assets/error.html                   479 B
  assets/logo.svg                     664 B
  assets/opensug2.js                 1242 B
  assets/simple.txt                 61622 B
```

**`assets/` 里没有 `homepage2.html`** → 它是**运行时生成/下载**的。

---

## 二、DEX 字符串表的关键命中

| 关键词 | 命中数 | 说明 |
| --- | --- | --- |
| `homepage2.html` | **有** | 文件名硬编码 |
| `homepage.css` | 有 | 同目录的样式文件 |
| `homepage.html` | 有 | 另一版本 |
| **`loadDataWithBaseURL`** | **0 条** | **Via 完全不用它** |
| `shouldInterceptRequest` | **1 条** | 出现了这个方法名 |
| `data:text/html,` | **有** | Via 在某些场景用 data URL |
| `text/html` | 有 | MIME 字符串 |
| `HomepageConfig` / `HomepageFavorite` | 有 | 主页配置类 |
| `homepageURL` / `homepage_url` | 有 | 配置项 |

**全部 homepage 相关字符串**：
```
*is in home page, change color for homepage
, homepage='
, homepageUrl='
// @homepageURL  
CREATE TABLE scripts (...)
Homepage:
HomepageConfig(isRtl=
HomepageFavorite(url=
homepage
homepage.css
homepage.html
homepage2.html
homepageURL
homepage_url
```

---

## 三、结论

### 3.1 Via 不用 `loadDataWithBaseURL`

**`loadDataWithBaseURL` 在 DEX 里零命中** → **Via 不是用"读文件内容再 loadData"的方式**。

**所以它加载主页的方式必然是**：
- `loadUrl("file://" + getFilesDir() + "/homepage2.html")`
- 或 `loadUrl("data:text/html,...")`（那个在容器内正常，W1 证明）

**而 `homepage2.html` 文件名硬编码 → 说明它写文件再 loadUrl。**

### 3.2 这与 W2 完全同构

| | 探针 W2 | Via |
| --- | --- | --- |
| 文件位置 | `getFilesDir()/wvprobe.html` | `getFilesDir()/homepage2.html` |
| 加载方式 | `loadUrl("file://" + path)` | **`loadUrl("file://" + path)`**（推断） |
| 容器外 | ✅ 正常渲染 | ✅ 正常（用户确认） |
| 容器内 | ❌ 显示源码 | ❌ 显示源码（用户确认） |

**→ 同一个缺陷，同一个机制。**

### 3.3 `shouldInterceptRequest` 出现了 1 次

**可能是**：
1. Via 自己实现了 `WebViewClient.shouldInterceptRequest`
2. 或只是某处引用了这个方法名（如反射、字符串常量）

**无法从字符串表判断**（需要 DEX 的 method 级解析）。

**这一点对方案 A 很关键**：
- 如果 Via **实现了** `shouldInterceptRequest` 并返回非 null → 容器的包装要正确处理合并
- 如果 Via **没实现** → 容器接管没有冲突

---

## 四、对方案 A 的影响

### 方案 A（hook `shouldInterceptRequest` 补 MIME）对 Via 有效

**理由**：
1. **Via 走 `loadUrl("file://")`** —— 这正好是 `shouldInterceptRequest` 能拦到的路径（W2 的 `INTERCEPT url=file://...` 就是证据）
2. **Via 即使实现了 `shouldInterceptRequest`**，容器只要**包一层**：
   ```
   容器的 client:
     resp = 用户的 client.shouldInterceptRequest(req)
     if (resp != null) return resp              // 尊重用户
     if (req.url.scheme == "file") return 容器自己读并给 MIME
     return null
   ```
3. **Via 的 `data:text/html` 场景本来就在容器内正常**（W1 证明）

### 但有一个前提要先确认

**Via 是否真的用 `loadUrl("file://")`** —— 需要 DEX 的 method 级解析（看 `loadUrl` 的调用点上的字符串常量）。

**更简单的验证**：**在容器内看 `shouldInterceptRequest` 是否被 Via 的请求触发**。

**方法**：容器 hook 上线后，跑 Via，看 `INTERCEPT url=file://...homepage2.html` 是否出现。

**或者**：**现在就能验证** —— 容器当前的 W2 已经证明 `file://` 请求**会走 `shouldInterceptRequest`**（探针的日志里有 `W2 INTERCEPT`）。

**所以只要 Via 用 `loadUrl("file://")`，容器一定能拦到。**

---

## 五、下一步

| # | 任务 | 目的 |
| --- | --- | --- |
| **1** | **方案 A 落地**：容器 hook `setWebViewClient`，包装用户的 client，对 `file://` 和 `file:///android_asset/` 补 MIME | 一次修好 W2/W3/W4 |
| **2** | 实现后在容器内跑 Via，看 `homepage2.html` 是否正常渲染 | 验收 |
| **3** | 如果 Via 仍有问题，再查它是否用了别的加载方式 | 兜底 |

**风险清单**：
- 容器包装 client 时，用户的 client 可能被替换过（`setWebViewClient` 多次调用）→ 要处理
- `file:///android_asset/` 要走**探针/guest 的 `getAssets()`**（W12 证明可用）
- 性能：每个子资源过一次 Java 层
