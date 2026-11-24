# 项目状态（STATUS）

> 最后更新：**2026-11-24 22:40（device）**
> 本文件是唯一「当前状态」入口。文档怎么找见 `docs/README.md`；历史证据见 `docs/输出/`；
> **被推翻的结论见 `docs/archive/错误结论-2026-11/`（引用任何结论前先对照一遍）**。

---

## 0. 一句话现状

容器已能跑 Via 与探针；**WebView 的 `file://` MIME 问题未解决**（Via 主页显示源码）。
本轮最大进展：**Java 层文件 I/O 重定向已打通**（v141 `RegisterNatives` + v142 guest 补注册规则）——
这是「假路径/路径保真/外部消费者」所有方案的共同地基，之前一直缺。
同时查清了**路径转换三层机制**与**注入时机审计**。

---

## 1. 本轮（2026-11-24 晚）确定的事实

| # | 事实 | 证据 |
| --- | --- | --- |
| **F1** | 沙箱 `.html` 经 `file://` 得到 `document.contentType = text/plain`；`/data/local/tmp` 的同一个 HTML 得到 `text/html` | 探针 W2 vs W14 |
| **F2** | 变量**不是** BOM、不是写入者、不是 `virtual/data` 路径段 | W35 四组合 / W17 / W18 |
| **F3** | 普通 `file://` 的 **Java `AndroidProtocolHandler.open()` 返回 null**（两条路径都是）→ Java 侧不参与 MIME 判定 | W34（真实签名是 `open(GURL)`） |
| **F4** | `libwebviewchromium.so` **未被容器 patch**（在 `/product/`，NS_HOOK_SYSTEM_LIBS=0） | logcat 无 patch 记录 |
| **F5** | WebView 认为的 `dataDir` 实际是**沙箱路径**（`url.startsWith(dataDir)=true`） | W36 |
| **F6** | **Gaia 里同一个探针 W2/W3 都正常** → 「沙箱路径必然嗅探」是错的，只有我们容器如此 | Gaia 对照记录 |
| **F7** | 容器**客主进程不装 hook**（P0-3 生效） | logcat `initNativeHooks skipped: container main process` |
| **F8** | **guest 进程的重定向规则表曾经是空的**（`rules=0`） | `<filesDir>/v141-javaio.log` |
| **F9** | `RegisterNatives` 替换 `libcore.io.Linux.open/access` **能装成功**（`installed=true`） | 同上 |
| **F11** | **Java 层路径重定向已打通**：guest 补注册后 `rules=11`，W38 写 `/sdcard` 成功且文件落在沙箱 `.../sdcard/wvprobe_javaio.txt` | breadcrumb `v142-redirect` + W38 |
| **F10** | v137（包装 `WebViewClient` 补 MIME）**时序上做不到**：`loadUrl` 早于任何视图树扫描 | `installed=0(+132ms) → installed=1(+10s)`、`delegated=0` |

## 2. 已确认的边界（**不要再重复调查**）

| # | 结论 | 记录 |
| --- | --- | --- |
| 1 | RenderThread SIGABRT 是容器 native hook 污染 GPU 驱动，不是 Adreno 缺陷 | `docs/输出/记录/记录-native-hook作用域与污染.md` |
| 2 | hook 作用域：容器自身 + 图形栈永不 patch；系统库默认不 patch（`NS_HOOK_SYSTEM_LIBS=0`） | 同上 |
| 3 | **Java 层文件 I/O 原先没有任何重定向**（`hookFileClass()` 是空实现） | `docs/架构/架构-路径转换机制摸底.md` |
| 4 | **普通 guest 没有 `/data/user/0/<guest>/` 规则**（只在 pathFidelity 注册） | 同上 |
| 5 | 容器内文字选择正常，问题只在 WebView（渲染在 isolated 进程） | `docs/输出/记录/记录-工程体检.md` |
| 6 | Gaia 沙箱在 `/data/user/0/<容器>/gaia/`（内部存储），且**不靠任何 WebView hook 就正常** | `docs/输出/记录/记录-Gaia内WebView对照…md` |
| 7 | 容器不属于 VirtualApp 家族；上游 = `TanvirHossain2/NEXTVM` | `docs/输出/记录/记录-上游溯源.md` |
| 8 | 两家沙箱根并存：`app_virtual/`（名义）vs `files/virtual/`（实际） | `docs/输出/记录/记录-双轨沙箱路径诊断.md` |
| 9 | **注入点 ≠ 生效进程**：`launch()`（主进程）里写的"环境设置"不会传到 guest | `docs/架构/架构-注入时机审计.md` |

## 3. 容器当前能力状态

详见 `docs/现状/README.md` 的「能力生效状态」表。要点：

| 能力 | 状态 |
| --- | --- |
| Binder 代理、`VirtualContext` 路径改写、native GOT patch | ✅ 生效 |
| guest 路径重定向规则 | ✅ **v142 起生效**（之前 `rules=0`） |
| Java 层文件 I/O 重定向 | ✅ **已打通（v141+v142）**：W38 写 `/sdcard` 成功、文件落沙箱 |
| v138 进程 Application → guest | ✅ 生效（W32 的 class 是 `ProbeApplication`） |
| **WebView `file://` MIME** | ❌ **未解决** |
| `AntiDetectionEngine` / `IdentitySpoofingEngine` / `FileAccessInterceptor` | ❌ 零调用 |

## 4. 当前未决问题

| # | 问题 | 现状 / 下一步 |
| --- | --- | --- |
| 1 | **WebView MIME** | 已知：Java 不参与（F3）、chromium 不 patch（F4）、URL 在 dataDir 内（F5）。剩下两条路：① 让 `file://` 落到 native 的标准分支（需改 dataDir，但**普通 guest 无规则兜底**，是短命补丁）；② 做宿主代读（W11 有效，但注入时机做不到，F10）。**详见 `docs/输出/记录/记录-W2-text-plain机制收敛.md`** |
| 2 | ~~Java 层重定向是否真的通了~~ | ✅ **已确认打通**（`rules=11` + W38 写 `/sdcard` 成功且落沙箱）。延伸项：扩到 `stat/lstat/readlink/unlink/rename/mkdir` |
| 3 | 注入位置是否还有漏项 | `setupPathFidelityRedirections` / `registerVirtualSdcard` / `installDeferredProxies` 三项待确认是否也要搬到 guest |
| 4 | 反检测何时接线 | 需先修 `AntiDetectionEngine` 两个缺陷（`is_path_hidden` 精确匹配、无 `readdir` 过滤） |

## 5. 设备与构建环境

| 项 | 值 |
| --- | --- |
| 测试设备 | `10AFA8169Z0039G`（vivo V2463A / Android 15 / arm64-v8a） |
| 设备时钟 | 比 PC 快约 1 分 51 秒；日志按**设备时间**切片 |
| 容器 | `1.0.0-<7位sha>`（`versionCode = GITHUB_RUN_NUMBER`） |
| 容器 uid | **每次干净安装会变**（`u0_a333` → `u0_a341`）→ **vivo 按 uid 管控日志，guest 的 `Log.i` 会被吞**（诊断请写文件，别依赖 logcat） |
| **appops 授权** | ⚠️ **每次干净安装（`uninstall` + `install`）都会清掉** `MANAGE_EXTERNAL_STORAGE`、`WRITE_EXTERNAL_STORAGE` 等授权。**结论**：① 干净安装后，任何"能写真实 `/sdcard`"的测试结果都不可信（授权没了）；② **不要拿干净安装前后的权限类测试做对比**；③ 需要授权的导入通道（`/sdcard/Download/`）在干净安装后要**重新授予**才可用，或改用 `/data/local/tmp/`（无需授权） |
| 签名 | 固定 keystore，SHA-256 `60:AD:A6:9E:…:40:B3:2C` |
| WebView | Chrome/126.0.6478.71 |
| 工具链 | Gradle 8.9 / NDK 27.1.12297006 / CMake 3.22.1 |

### CI 纪律（用户明确要求）

> **一次只触发一个编译 CI，且要明确指定是容器还是探针。**

```powershell
node tools/release/gh-api.mjs --dispatch probe-build.yml main       # 探针
node tools/release/gh-api.mjs --dispatch container-build.yml main   # 容器
```

- `push` **不会**触发任何编译（两个 workflow 已去掉 `push` 触发）
- 触发后不要轮询阻塞；需要结果时单独查一次
- 当前容器 workflow 的 **Unit tests 步骤被临时 `if: false` 暂停**（加快迭代），恢复方式写在 workflow 注释里

## 6. 文档导航

| 想了解 | 去哪 |
| --- | --- |
| 文档总索引 / 写作约定 | `docs/README.md` |
| 系统怎么运作 | `docs/架构/README.md` |
| 为什么这么设计 | `docs/设计/README.md` |
| 现在什么生效、什么没生效 | `docs/现状/README.md` |
| 做什么、什么顺序 | `docs/计划/README.md` |
| 证据、变更、调研、调试手册 | `docs/输出/README.md` |
| **哪些结论是错的** | `docs/archive/错误结论-2026-11/README.md` |

## 7. 开工前必读的排查顺序

> **guest 行为异常但没崩溃/没栈时，先看 `GuestBreadcrumb`**（logcat tag = `BootCrumb`，
> 文件在 `<沙箱>/files/breadcrumb-<pid>.log`），再看探针 P/N/W 系列，**最后**才看 native 栈。

上游 NEXTVM 埋了 199 处 breadcrumb（有序、带时间戳、含被吞异常完整栈）。

**本项目绕过的弯路都是因为跳过它、从结果倒推**：

| 弯路 | 错误归因 | 真实原因 |
| --- | --- | --- |
| RenderThread SIGABRT | Adreno 驱动缺陷 | 容器 native hook 污染 GPU 驱动 |
| 长按 ANR | `WindowManager`/`PopupWindow` | 只在 WebView（isolated 进程）出现 |
| Via 主页源码 | MIME / WebView 库未 patch / 沙箱该放外部存储 | 都不是：变量在「路径落在哪」，机制在 native 侧（见 F1–F6） |
