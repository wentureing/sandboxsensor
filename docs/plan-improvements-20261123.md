# 架构缺点评估 + 提升计划

- 日期：2026-11-23
- 目标（用户明确）：**WebView 支持**、**debug 容器自身的自动化简单测试**
- 前置：`project-health-20261122.md`（体检）、`app-virtual-vs-files-virtual-20261123.md`（双轨路径诊断）

---

## 一、架构缺点清单（按"对两个目标的阻碍度"排序）

| # | 缺点 | 硬证据 | 阻碍 WebView | 阻碍 debug 自测 |
| --- | --- | --- | --- | --- |
| **1** | **没有任何测试基础设施** | `src/test` / `src/androidTest` / `src/debug` 全空白 | 低 | **极高** |
| **2** | **没有"可断言"的诊断出口** | `GuestBreadcrumb`/`StateProbe` 只采集，不自检 | 中 | **极高** |
| **3** | **拿不到容器内产物** | `CmdReceiver` 只有 5 条命令，无导出；`/sdcard` 副通道落到沙箱镜像 | 低 | **高** |
| **4** | **两家沙箱根并存** | `app_virtual/`（`SandboxManager`）vs `files/virtual/`（`VirtualEngine`） | 低 | **高**（测哪个才对） |
| **5** | **超大文件** | `native-hook.cpp` 6,458 / `VirtualApplicationManager` 4,309 / `SystemServiceProxyManager` 3,273 / `ActivityThreadHook` 2,133 | **中**（新 hook 无处安放） | **高**（无法单测） |
| **6** | **`VirtualEngine` 双向耦合** | 5 条虚线回指（`VirtualApplicationManager`/`ActivityThreadHook`/`NativeHookBridge`/`PackageManagerProxy`/`ActivityManagerProxy` 都反向依赖引擎） | 中 | **高**（测试要 mock 一整圈） |
| **7** | **`SandboxManager` 被重度依赖但路径错** | `VirtualContext` 8 处调用；`getExternalFilesDir` 走 `app_virtual` | 低 | **高** |
| **8** | **`feature/*` 直接依赖 `core:virtualization`** | 4 个 feature 模块 | 低 | 中 |
| **9** | **WebView 渲染在 isolated 进程**（客观限制，非代码缺点） | `webview_zygote` → `sandboxed_process0` | **极高** | — |

---

## 二、对两个目标的逐项影响

### 目标 A：WebView 支持

**真正的主要阻碍是 #9（客观限制），不是代码缺点。**

| 分析项 | 结论 |
| --- | --- |
| 架构缺点是否挡住 WebView？ | **不挡**。#1–#8 都不影响"能不能做"，只影响"做完好不好维护" |
| 需要的新架构元素 | 一个能挂"WebView 请求拦截"的位置。当前没有合适的地方（`native-hook` 是 native 层，管不了 Java 的 `WebViewClient`） |
| 需要的观测能力 | **`DiagnosticsSink`（#2）**——否则加了拦截也看不出生没生效 |
| 建议形态 | 独立 `:core:webview` 模块，只做 `HostReadPolicy` + `WebResourceResponse` 适配；**不改 `VirtualEngine`** |
| 结论 | **#2 是前置**，其余缺点不阻塞 |

### 目标 B：debug 容器自身自动化简单测试

**这个目标的阻碍**几乎全部**是**架构缺点。

| 阻碍 | 具体表现 | 补法 |
| --- | --- | --- |
| #1 无测试源集 | 想写 `JUnit` 都没地方放 | 建 `src/test`（Robolectric）+ `:core:diagnostics` 模块 |
| #2 无诊断出口 | 无法"断言容器做对了什么" | `DiagnosticsSink` + `SelfCheckRunner` |
| #3 拿不到产物 | 探针报告只能靠 logcat 猜 | `probe-out` + `COLLECT_OUT`/`LIST_OUT`/`CAT_OUT` |
| #4 两家沙箱 | 不知道哪个是真根 | **先实测确认**（已确认：`files/virtual`），并在自检里断言"两家根不一致"这个已知状态 |
| #5 超大文件 | `VirtualApplicationManager` 的 51 个函数无法逐个测 | 只测**纯函数**（路径/MIME/APK 解析），不碰巨型类 |
| #6 双向耦合 | 单测要构造整条链 | 用 `SelfCheckRunner`（在真实进程里跑）代替隔离单测 |

**结论**：**#1–#4 是必须先补的四个短板**。

---

## 三、提升计划

### 阶段划分

```
P0（基础，必须先做）
  P0-1  DiagnosticsSink 接口 + NoOp            ← 纯增量，零风险
  P0-2  probe-out + CmdReceiver 三命令         ← 拿数据
P1（能力）
  P1-1  :core:diagnostics 模块 + SelfCheckRunner
  P1-2  HostReadPolicy + Robolectric 单测      ← 安全边界，纯函数最好测
  P1-3  WebView 宿主代读（最小版）
P2（长期，不阻塞）
  P2-1  收敛双轨路径（带迁移）
  P2-2  拆 natural-hook.cpp / SystemServiceProxyManager
  P2-3  打破 VirtualEngine 双向耦合（抽 EngineHost 接口）
```

### P0-1：`DiagnosticsSink`（0.5 天）

| 项 | 内容 |
| --- | --- |
| 改动 | `core/virtualization/src/main/.../diag/DiagnosticsSink.kt`（新增） |
| 规模 | 1 文件 / ~60 行 |
| 风险 | **零**（纯新增，release 里是 NoOp） |
| 验证 | 编译通过 + `Diagnostics.sink` 默认是 `NoOp` |

### P0-2：`probe-out` + 三命令（0.75 天）

| 项 | 内容 |
| --- | --- |
| 改动 | `SandboxManager.collectProbeOutput()`（新增）/ `CmdReceiver`（3 命令） |
| 规模 | 2 文件 / ~150 行 |
| 风险 | 低（新目录 + 新命令，不改现有逻辑） |
| 验证 | 见 `plan-probe-output-dump.md` §8 |

### P1-1：`:core:diagnostics` + `SelfCheckRunner`（1.5 天）

| 项 | 内容 |
| --- | --- |
| 改动 | 新模块 + `app/src/debug/DebugInit.kt` + 6 项自检 |
| 规模 | 4 文件 / ~400 行 + Gradle |
| 风险 | 中（动 Gradle，跨模块） |
| 验证 | debug 包启动 5 秒后 `files/probe-out/selfcheck.json` 出现 6 项结果 |

### P1-2：`HostReadPolicy` + 单测（1 天）

| 项 | 内容 |
| --- | --- |
| 改动 | 新类 + `src/test` 下的 Robolectric 测试 |
| 规模 | 2 文件 / ~200 行（含 15+ 条测试用例） |
| 风险 | 低（纯函数） |
| 验证 | 覆盖：`..` 逃逸、前缀攻击、跨实例、软链、合法路径 |

### P1-3：WebView 宿主代读最小版（2–3 天）

| 项 | 内容 |
| --- | --- |
| 范围 | 只拦 `file://` 的 `html/css/js/png/jpg/svg`；补 `Content-Type`/`Content-Length`；**不做 range/缓存/content://** |
| 改动 | `:core:webview` 新模块 |
| 前置 | **必须先确认 Gaia 的机制**（v1.8 探针实验：写 html 到自己 filesDir + Via 打开） |
| 风险 | 中高（黑盒调试 + MIME/编码/相对路径四件套） |

### 验收方式（贯穿所有阶段）

| 阶段 | 验收 |
| --- | --- |
| P0-1 | 编译 + `Diagnostics.isInstalled == false` |
| P0-2 | `COLLECT_OUT` → `LIST_OUT` → `CAT_OUT` 三步能取到与 `run-as` 一致的 JSON |
| P1-1 | debug 包自检 6 项全绿；release 包无 `:core:diagnostics` 类 |
| P1-2 | 单元测试全绿，含 5 类攻击用例 |
| P1-3 | Via 主页正常渲染 + 长按有菜单 + `P21` 能看到宿主 fd |

---

## 四、当前最该先做的一件事

> **P0-1 + P0-2 加起来 1.25 天，风险接近零，但把"能观测/能断言"这条路打通。**
> 没有它，后面无论做 WebView 还是自检，都会退回到"看 logcat 反推"。

**所以顺序就是 P0-1 → P0-2 → P1-2（HostReadPolicy，最安全）→ P1-1 → P1-3**。

`P1-2` 提前到 `P1-1` 前面，因为它是纯函数、零依赖、且是安全边界——**在没有测试基础设施时，它是最容易先落地的一块测试**。
