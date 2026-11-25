# 现状（`docs/现状/`）

> 回答：**现在到底是什么样**——能力是否真的生效、哪些代码是死的。
> 这是「事实快照」，会随代码变化而失效，**每次大改后要回来更新**。

---

## 一、文档

| 文档 | 内容 | 时效 |
| --- | --- | --- |
| **`零调用扫描报告.md`** | 哪些类/方法写了但**从未被调用**（含行数、调用点判定） | 2026-11-24 快照 |
| **`索引.md`** | 旧版调研索引（历史入口，内容已并入本目录与 `docs/README.md`） | 2026-11-24 |

## 二、能力生效状态（2026-11-25 快照）

> 判据：**"代码存在" ≠ "生效"**。本表只记"实测/代码证据确认生效"的项。

| 能力 | 状态 | 证据 / 备注 |
| --- | --- | --- |
| Binder 代理（AM/PM/系统服务） | ✅ 生效 | `installAllProxies` 在 `VirtualEngine.initialize()`（每进程） |
| `VirtualContext` 路径改写（26 个 override） | ✅ 生效 | 探针 W36：`getFilesDir()` 返回沙箱真路径 |
| native GOT/PLT patch（被 patch 的 so） | ✅ 生效 | 架构文档 + `NS_HOOK_SYSTEM_LIBS=0` 日志 |
| **路径重定向规则（guest 进程）** | ✅ **v142 起生效** | 之前 `rules=0`（规则只在主进程注册） |
| **native hook 覆盖面** | ✅ 已实现，但**默认生效面有限** | 27 条符号（`native-hook.cpp:1679-1705`，含 `mkdir`/`mkdirat`/`unlink`/`unlinkat`/`rename`）+ 16 个 Java `RegisterNatives` 方法（`debug.nextvm.javaIo=0` → **默认不安装**）。库级过滤：容器自身/图形栈永不 patch，系统库默认不 patch（`NS_HOOK_SYSTEM_LIBS=0`）。**默认配置下 Java 层落宿主、native 层落沙箱** → 见 `../架构/架构-hook实现现状-代码为准.md` |
| **多实例** | ⏸ **开发暂停** | 合成路径不含 instanceId（`ProcessSlot.kt:193`），同包两份会互串；用户 2026-11-25 决定暂停，B 阶段不加 slot 签名 |
| **路径欺骗豁免（A 阶段，`6b5cfbe`）** | ⚠️ **已合并，未装包验证** | `PathSpoofConfig` / `PathPolicy`：豁免 = 不产生合成路径、**保留**沙箱隔离；配置 `<filesDir>/path_spoof.conf`（首次生成默认含 `mark.via`）；`CmdReceiver` 三命令 `SPOOF_EXEMPT_ADD/REMOVE/LIST`；改完需重启。单测 H1–H10 全绿，真机未验证（设备仍是 `b992ba4`）。见 `../代码镜像/core/model/PathPolicy.md` |
| **合成路径的「族根」翻译** | ✅ **已修（`11f23d5` / `031eba8`）** | ROOT_FORM 匹配语义：以 `/` 结尾的规则同时拥有"去尾斜杠的根形态"，Java 与 C++ 同改；T39/T41/T42/T43 全绿（CI `45579005086`）。规则集合未变 |
| **Java 层文件 I/O 重定向（v141）** | ⚠️ **已实现但默认关闭** | `RegisterNatives` 替换 `libcore.io.Linux.open/access` 能装上（W38 曾通过），但会让 Via **完全不能交互**（用户对照确认）。根因是异常语义与 AOSP 不一致，详见 `../输出/记录/记录-JavaIoHook导致Via不可交互.md`。开关：`debug.nextvm.javaIo`（默认 0） |
| `/proc/self` spoof、进程名、seccomp | ✅ 生效 | `ensureGuestApplicationBooted`（guest） |
| `WebViewWarmup`（provider 预热/修复） | ✅ 生效 | guest bind complete 时调度 |
| v138 进程 Application → guest | ✅ 生效 | 探针 W32：`currentApplication()` 的 class 是 `ProbeApplication` |
| **WebView `file://` 的 MIME** | ❌ **未解决** | 沙箱 `.html` → `text/plain`（Via 主页显示源码） |
| `AntiDetectionEngine`（805 行 / 40+ 处 hide/spoof） | ❌ **零调用** | 见 `零调用扫描报告.md` |
| `IdentitySpoofingEngine`（`Build.SERIAL` 等） | ❌ **零调用** | README 明示"未应用" |
| `FileAccessInterceptor`（fake content / listFiles） | ❌ **零调用** | v141 另写了 native 实现 |
| `ensureWebViewResources`（WebView 资源注入） | ❌ **零调用，且刻意不调** | 接线过，导致 Via boot loop，已回退 |

## 三、设备/构建环境快照

| 项 | 值 |
| --- | --- |
| 测试设备 | `10AFA8169Z0039G`（vivo V2463A / Android 15 / arm64-v8a / PAGESIZE=4096） |
| 容器版本 | `1.0.0-<7位sha>`（`versionCode = GITHUB_RUN_NUMBER`，递增） |
| 容器 uid | 每次干净安装会变（如 `u0_a333` → `u0_a341`）→ **vivo 的日志管控按 uid 生效，guest 的 `Log.i` 可能被吞** |
| **appops 授权** | ⚠️ **干净安装会清掉** `MANAGE_EXTERNAL_STORAGE` / `WRITE_EXTERNAL_STORAGE` 等授权 → 干净安装前后的**权限类测试结果不可对比**；需要授权的通道（`/sdcard/Download/`）要重新授予，或改用 `/data/local/tmp/`（无需授权） |
| 签名 | 固定 keystore，SHA-256 `60:AD:A6:…:40:B3:2C` |
| WebView | Chrome/126.0.6478.71（`/product/app/WebViewGoogle64/`） |
| Gradle / NDK | Gradle 8.9 / NDK 27.1.12297006 / CMake 3.22.1 |

## 四、维护约定

1. 本目录只放**可核对的事实**（带命令/日志/探针编号），不放推测
2. 状态从 ✅ 变 ❌、或反之，**必须在同一提交里更新本表**
3. 快照要写日期；超过一个大版本（Android 大版本或容器大改）就重扫
