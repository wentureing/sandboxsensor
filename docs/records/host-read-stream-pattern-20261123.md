# "宿主代读 + stream 跨进程"：隐蔽性、可复用场景、心智压力

- 日期：2026-11-23
- 背景：Gaia 用这套模式让 Via 的 `file://` 在容器内正常工作（推论，待 v1.8 实验确认）
- 核心模式：**把"权限受限方"的文件访问，转交给"有权限方"，通过 stream / fd 跨进程传递**

---

## 一、隐蔽性：分三层看，结论是「中等」

### ① 从 WebView 内部（JS 层）看：**几乎不可检测**

| 检测手段 | 结果 |
| --- | --- |
| `location.href` | 仍是 `file:///原路径` ✓ 不变 |
| `document.title` / DOM | 完全正常 |
| `performance.getEntries()` | 资源条目存在；`transferSize` 在 `file://` 下本来就是 0，差异不明显 |
| 同源策略 / CSP | 与真实 `file://` 一致（响应 URL 就是请求 URL） |
| `fetch('file:///...')` 同一文件 | 走同一个 `shouldInterceptRequest` → 返回同样的内容 ✓ |

**所以网页本身查不出来**。

### ② 从 guest 的 Java 代码看：**可以检测**

| 检测点 | 说明 |
| --- | --- |
| **`WebViewClient` 是否被替换** | 如果 guest 自己设过 client，容器再包一层，guest 无法直接 `getWebViewClient()`（没有这个 API），但可以通过**行为差异**察觉：比如它 override 了 `shouldInterceptRequest` 并返回 null，期望走默认加载，结果却拿到了内容 |
| **`WebResourceResponse` 的 header** | 真实 `file://` 响应有 `Content-Length`、`Last-Modified`；宿主手造的 stream 若缺这些，**JS 的 `fetch().headers.get()` 能看出来** |
| **`InputStream` 类型** | 若宿主返回 `FileInputStream`，guest 理论上能 `getFD()` 拿到 fd；但通常包装成 `ByteArrayInputStream` 或 `ParcelFileDescriptor.AutoCloseInputStream` |
| **`shouldInterceptRequest` 的调用次数** | 真实加载每次请求调用一次；若宿主实现有 bug（重复拦截），次数会异常 |

**结论**：**主动检测的 guest 能发现**，普通应用不会。

### ③ 从系统/native 看：**宿主的 fd 会出现在宿主的 `/proc/self/fd`**

- guest 进程看不到宿主的 fd（不同进程）
- 但宿主自己的 `/proc/self/fd` 会显示 `.../真实路径`
- → **如果容器有 fd 泄漏检测（`P21`），会看到容器主进程打开了 guest 的沙箱文件**

**对我们自己的探针**：`P21`（fd targets）能观察到这种模式。

---

## 二、隐蔽性的关键弱点（如果要做对抗）

| 弱点 | 后果 | 缓解 |
| --- | --- | --- |
| **响应头不完整** | JS 可通过 `fetch().headers` 比对 | 补 `Content-Length`、`Last-Modified` |
| **`WebViewClient` 被透明替换** | 若 guest 设了 client 并依赖其返回值，行为会变 | 包一层 → 先调 guest 的 client，返回 null 时才走宿主代读 |
| **大文件用 ByteArray** | OOM（[WebView2 #4294](https://github.com/MicrosoftEdge/WebView2Feedback/issues/4294) 同类问题） | 用 `ParcelFileDescriptor.AutoCloseInputStream` 流式返回 |
| **同步 IO 在 `shouldInterceptRequest`** | 主线程/IO 线程卡死（[腾讯云问答](https://cloud.tencent.com.cn/developer/ask/sof/112039725) 提到"冻结 WebView"） | 宿主侧用独立线程 + 预读缓存 |

---

## 三、这个模式还能用在哪（按对本项目的价值排序）

模式本质：**权限受限方需要文件 → 有权限方代读 → 通过 fd/stream 交付**。

### ✅ 高价值（isolated 进程 / 跨权限场景）

| 场景 | 为什么需要 | 实现 |
| --- | --- | --- |
| **WebView 的 `file://`** | renderer 是 isolated uid，读不到容器沙箱 | `WebResourceResponse` |
| **WebView 的 `content://`** | 同上，且需要 URI 权限 | 同上 |
| **WebView 的 `assets/`** | APK 内资源，renderer 需要宿主解包 | 同上 |
| **`Blob` / `FileReader` 读取沙箱文件** | 同样是 renderer 侧 | 同上（同一入口） |

### ⚠️ 中等价值（guest 主进程能读，但路径不对）

| 场景 | 说明 |
| --- | --- |
| **`BitmapFactory.decodeFile(path)`** | guest 主进程有权限，通常**不需要**；只有路径指向容器私有位置且被隔离时才需要 → 改用 `decodeFileDescriptor` |
| **`MediaPlayer.setDataSource(path)`** | 同上 → 改用 `setDataSource(FileDescriptor)` |
| **`ExoPlayer` 的 `FileDataSource`** | 同上 → 换成 `ContentDataSource` + 宿主 fd |

### ❌ 低价值（本来就通的）

- guest 主进程的普通 `open`/`read`（容器已用路径翻译解决）
- `SQLiteDatabase`（同进程，路径翻译即可）

### 🔁 更广义的同构模式

这个"代读"思路其实可以推广到**任何"权限受限的跨边界访问"**：

| 边界 | 代读者 | 机制 |
| --- | --- | --- |
| isolated 进程 ↔ 宿主沙箱 | 宿主 | fd / stream |
| 应用 ↔ 其他应用的私有目录 | `FileProvider` / `ContentProvider` | `ParcelFileDescriptor` |
| 应用 ↔ 系统受保护资源 | 系统服务 | Binder |
| 多用户 / Work Profile | 系统 | `CrossProfileApps` / fd |

**共同点**：**跨越 UID 边界时，fd 是唯一不需要对方有路径权限的载体**。

---

## 四、心智压力（这是最该权衡的部分）

### 4.1 实现复杂度

| 项 | 难度 | 说明 |
| --- | --- | --- |
| **hook `WebViewClient`** | **高** | 需要拦截 framework 方法（`WebView.setWebViewClient`），或用 View 树遍历 + 反射替换 |
| MIME 推断 | 中 | 需要维护扩展名 → MIME 表（`.html` / `.css` / `.js` / `.png`…） |
| 编码处理 | 中 | `charset` 从 HTML meta 或默认 utf-8 |
| 相对路径解析 | **中高** | HTML 里的 `href="a.css"` 需要按 baseUrl 解析成绝对路径，再拦截 |
| 大文件流式 | 中 | 必须用 `ParcelFileDescriptor` 而不是 `ByteArrayInputStream` |
| range 请求 | 中 | 视频 `<video>` 会发 `Range`，需要正确处理 |
| 缓存 | 中 | 不缓存 → 每次 IO；缓存 → 一致性问题 |
| **调试** | **高** | guest 是黑盒，只能看 logcat；出问题难以复现 |

### 4.2 心智负担

1. **必须理解 WebView 多进程架构**（否则会把"hook 主进程"当成"hook 渲染进程"，就像我们之前那次）
2. **必须理解 isolated uid 的权限边界**（否则会像我们一样误以为"沙箱放外部就行"）
3. **必须处理 MIME/编码/相对路径/range** 四件套，任一漏掉就是"白屏"或"源码"
4. **兼容性**：WebView 版本差异、厂商 ROM 差异
5. **安全责任转移**：宿主代读意味着**宿主替 guest 打开了它本不该打开的文件** → 必须自己实现"哪些路径允许代读"的白名单，否则等于给 guest 开了后门

### 4.3 好处

| 好处 | 说明 |
| --- | --- |
| **一次解决一类问题** | 所有 isolated 进程的文件访问都能用同一套 |
| **不依赖系统版本** | 纯 Java 层，不碰 SELinux/ROM 差异 |
| **可精细控制** | 能加白名单、能注入、能改写内容 |
| **可观测** | 宿主能记录每次代读，便于审计 |
| **对 JS 隐蔽** | 网页层几乎无法区分 |

---

## 五、结论

| 问题 | 答案 |
| --- | --- |
| **是否隐蔽** | **对 JS 隐蔽，对主动检测的 guest 不隐蔽**；关键看响应头是否补齐 |
| **还能用在哪** | 高价值：所有 isolated 进程的文件访问（WebView 的 `file://`/`content://`/`assets`/`Blob`）；中等价值：`BitmapFactory`/`MediaPlayer`/`ExoPlayer` 的 fd 变体 |
| **心智压力** | **高**。四件套（MIME/编码/相对路径/range）+ hook framework 方法 + 黑盒调试 |
| **值不值** | 如果目标只是"让 1–2 个 WebView 类应用能用"，**先做最小版（只拦 `file://` + html/css/js/png）**；如果要做成通用能力，成本接近重写一个资源加载层 |

### 一个务实的判断

我们现在的处境：

```
Gaia 已经做到了（Via 主页正常）
  → 说明这条路可行，不需要再论证"能不能"
  → 真正的问题是"我们要不要为它投入"

最小可行版的边界：
  ✅ file:// 的 html/css/js/png/jpg/svg
  ✅ 补齐 Content-Type + Content-Length
  ❌ 不做 range（视频）、不做缓存、不做 content://
  → 覆盖 80% 的"本地页"场景，成本约 2–3 天
```

**关键前提未验证**：Gaia 到底是不是用这个机制。v1.8 的探针实验（写 html 到自己 filesDir + Via 打开）能一次定性。
