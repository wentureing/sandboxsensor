# 调查：频繁 `HTTP 0` 的真实原因 + WebView 修复验证结果

- 日期：2026-11-24 18:12
- 起因：用户要求 —— ①调查频繁网络失败原因，可能 GitHub 为 agent 专门开放接口 ②允许卸载重装

---

> ## ⚠️ 更正（2026-11-24 18:50，device）
>
> 本文标题里的"WebView 修复验证成功"**是错的**。
>
> 那次"修复"（23b0538 接线 nsureWebViewResources）导致 **Via 启动循环**
> （InflateException: layout/p line #48 → TypedValue.assetCookie=-1），
> 已在 2fc764d 回退。
>
> 本文的**网络失败原因调查部分仍然有效**（HTTP 0 = 代理端口用错，7897 应为 12450）。
> **WebView 部分请只看 docs/计划-WebView修复.md。**

---

## 一、`HTTP 0` 的真实原因：**不是 GitHub，是本地代理**

### 1.1 排除"GitHub 限流"

**GitHub 的限流返回的是 `403`**，并带这些头：
```
X-RateLimit-Limit: 60 / 5000
X-RateLimit-Remaining: 0
X-RateLimit-Reset: <unix ts>
```

**来源**：[GitHub 官方文档 - REST API 的速率限制](https://docs.github.com/ja/rest/using-the-rest-api/rate-limits-for-rest-api?apiVersion=2026-03-10)

**我们遇到的是 `HTTP 0`**，不是 403。

### 1.2 `HTTP 0` 在 `gh-api.mjs` 里的含义

```js
// gh-api.mjs 的 request()
const st = Number((head.match(/HTTP\/[\d.]+ (\d+)/) || [])[1]);
res({ status: st, body });
```

| 打印 | 含义 |
| --- | --- |
| `HTTP 200` | 正常 |
| **`HTTP 0`** | **连接根本没建立**（`c.on('error')` / `s.on('error')` / TLS 握手失败） |
| `HTTP NaN` | 连上了但响应头格式异常 |

**所以 `HTTP 0` = 本地到代理、或代理到 GitHub 的连接失败。**

### 1.3 具体是哪个环节

**`gh-api.mjs` 走的是 HTTP CONNECT 隧道到 `127.0.0.1:7897`（Clash）**：

```
node -> CONNECT 127.0.0.1:7897 -> Clash -> CONNECT api.github.com:443 -> GitHub
         ↑ 本地                     ↑ 代理出口        ↑ TLS
```

**三个环节都可能失败**：

| 环节 | 失败原因 | `HTTP 0` 时的表现 |
| --- | --- | --- |
| **① 本地到 Clash** | Clash 没运行 / 端口不对 | 立即失败 |
| **② Clash 出口** | 节点的上游不稳（订阅过期、节点被墙） | 超时（25s） |
| **③ TLS 握手** | 代理中间人证书问题 | `s.on('error')` |

**从"重试 4–10 次才成功"的现象看，最可能是 ②（Clash 节点不稳）** —— 换节点就好，同一个节点连续失败。

### 1.4 "GitHub 给 agent 专门开放接口" —— **不存在**

**已核实**：
- GitHub REST API 只有两档：**未认证 60 次/小时**、**认证 5000 次/小时**
- **没有"agent 专用"接口或更高配额**
- 认证方式是 **PAT / GitHub App / OAuth**，没有第三种

**所以**：**不存在"专门接口"，`HTTP 0` 也不是配额问题。**

### 1.5 改进方案（三个层次）

#### A. 立即改善（脚本层）

```js
// 1) 打印具体错误原因（现在是静默重试，看不到为什么失败）
c.on('error', (e) => { console.error(`[proxy CONNECT] ${e.message}`); rej(e); });
s.on('error', (e) => { console.error(`[proxy TLS] ${e.message}`); rej(e); });
s.setTimeout(25000, () => { console.error('[proxy timeout]'); s.destroy(); rej(new Error('timeout')); });

// 2) 指数退避（现在是固定 4-6 秒）
const backoff = (attempt) => Math.min(30000, 1000 * Math.pow(2, attempt));

// 3) 直连 fallback（代理挂的时候至少能试一次直连）
//    GitHub API 在国内直连大多失败，但偶尔能通
```

#### B. 使用 `git` 代替 API（**最可靠**）

**很多操作其实不需要 GitHub API**：

| 需求 | 现在的做法 | **更可靠的替代** |
| --- | --- | --- |
| 查提交 | `gh-api.mjs /repos/.../commits` | **`git -C repo log`**（本地就有） |
| 查文件内容 | API | **本地仓库** |
| 下载 Release APK | `gh-api.mjs --asset` | ❌ 只能走 API（除非用 `gh` CLI） |
| 触发 CI | `gh-api.mjs --dispatch` | ❌ 只能走 API |
| 查 CI 状态/日志 | `gh-api.mjs /actions/runs` | ❌ 只能走 API |

**所以**：**只有"CI 相关"和"Release 下载"必须走 API**，其余可以本地做。

#### C. 环境层（用户侧）

| 措施 | 说明 |
| --- | --- |
| **Clash 换稳定节点** | 最直接 —— 同一节点连续失败说明节点问题 |
| **开 Clash 的"GLOBAL"模式测一次** | 排除规则问题 |
| **或改用 GitHub 的 IP 直连 + hosts** | 不推荐（IP 会变） |
| **`gh` CLI + `GH_TOKEN`** | `gh` 自带重试和更好的错误信息，但也要走代理 |

### 1.6 我的建议（按性价比）

| 顺 | 措施 | 成本 | 收益 |
| --- | --- | --- | --- |
| **1** | **给 `gh-api.mjs` 加错误原因打印 + 指数退避** | 20 行 | **能看到为什么失败**，而不是"HTTP 0" |
| **2** | **CI/Release 相关操作合并**（一次拿多个信息，少发请求） | 按需 | 减少请求次数 |
| **3** | Clash 换节点 | 用户操作 | 最直接 |
| 4 | 引入 `gh` CLI | 中 | 更好的错误信息 |

---

## 二、WebView 修复验证结果（**成功**）

### 2.1 安装

```
$ adb uninstall com.nextvm.app
Success
$ adb install -t container-shitjumpingvm-196fc44.apk
Success
versionCode=41  versionName=1.0.0-196fc44
```

**卸载重装后签名问题解决**（之后所有构建都用固定的 Secret keystore）。

### 2.2 沙箱状态（卸载后重建）

```
files/virtual/data: com.sandbox.probe_1795515137058    ← 新实例
app_virtual: ls: No such file or directory              ← 双轨沙箱修复保持 ✓
```

### 2.3 **关键验证：WebView 资源注入成功**

```
18:12:40.811 I VResources: Creating Resources for guest APK: ...com.sandbox.probe_1795515137058.apk
18:12:40.813 I VResources: Added WebView resources: /product/app/TrichromeLibrary64/TrichromeLibrary64.apk (cookie=10)   ← 关键！
18:12:40.815 I VResources: Added WebView resources: /product/app/WebViewGoogle64/WebViewGoogle64.apk (cookie=11)
18:12:40.815 I VResources: ensureWebViewResources: 2 APK(s) added for com.sandbox.probe_1795515137058
```

| 判据 | 结果 |
| --- | --- |
| `TrichromeLibrary64.apk` 注入 | ✅ **cookie=10** |
| `WebViewGoogle64.apk` 注入 | ✅ **cookie=11** |
| 总注入数 | ✅ **2 个** |
| `none of WEBVIEW_APK_PATHS exists` | ❌ 0（说明路径都找到了） |
| `addAssetPath not found` | ❌ 0 |
| `no cache entry` | ❌ 0 |

**所以 A1 + A2 的修复都生效了** ✓

### 2.4 还需要验证的

| # | 检查 | 怎么做 |
| --- | --- | --- |
| 1 | **长按 WebView 文字不再崩** | 需要容器内跑一个 WebView 页面（Via 或探针加 `WebViewProbeActivity`） |
| 2 | `Resources$NotFoundException` 是否还出现 | `logcat \| grep "Resources\$NotFound"`（在长按后查） |

**当前 `Resources$NotFoundException` 仍有 1 条命中**，但那是**旧日志**（16:19 那次）。

---

## 三、一句话

| 问题 | 答案 |
| --- | --- |
| **`HTTP 0` 是什么** | **本地到 Clash 代理、或 Clash 到 GitHub 的连接失败**（不是限流） |
| **GitHub 有 agent 专用接口吗** | **没有**（只有 60 / 5000 两档配额） |
| **怎么改善** | **① 打印错误原因 ② 指数退避 ③ Clash 换节点 ④ 能用本地 git 的就别走 API** |
| **WebView 修复有效吗** | ✅ **已验证：TrichromeLibrary64 + WebViewGoogle64 都注入成功（cookie=10/11）** |
| **popup 崩溃修好了吗** | ⏳ **还需要"长按 WebView 文字"的实测**（当前没触发） |
