# 架构：主原生 / guest 全 hook（含 hook 手段的必要性重估）

- 日期：2026-11-24
- 起因：用户提出 —— 在新设计下"主原生，guest hook"：
  **guest 读取真实文件是无必要的**，只需用户手动为 guest 利用主线程"导入"文件（而且通常不需要）；
  guest 完完全全被 hook 应该可行；WebView 及类似需求由主线程代读应该可行（估计代价）；
  更新 hook 手段和必要性，以及用到的层次位置
- 结论先给：**这个收缩让架构简单了一大截** —— 不需要 `RealPathService` 的 fd 跨进程传递，
  **"导入 = 主进程读真实文件 → 拷贝到沙箱"** 就够了

---

## 一、核心判断的精确化

### 1.1 "guest 完全不读真实文件"要精确成一句话

**不准确的说法**：guest 完全不读真实路径。

**准确的说法**：
> **guest 只读「系统路径」和「自己的沙箱」，不读「容器的私有路径」和「用户的真实文件」。**

| 类别 | 例子 | guest 能读吗 | 处理 |
| --- | --- | --- | --- |
| **系统路径** | `/system`、`/vendor`、`/apex`、`/system/lib64` | ✅ **必须能读** | **不重定向**（现状：`NS_HOOK_SYSTEM_LIBS=0` 已这样） |
| **自己的沙箱** | `<沙箱>/data/<instance>`、`<沙箱>/sdcard` | ✅ 读 | `dataDir`/`externalDir` 已指向这里 |
| **容器私有路径** | `/data/user/0/com.nextvm.app/...` | ❌ **不该读** | `hidePath` 隐藏 |
| **用户真实文件** | `/sdcard/Download/x.pdf` | ❌ **不该直接读** | **用户通过"导入"把它拷进沙箱** |
| **`/proc/self/*`** | maps、cmdline、status | ⚠️ 读**伪造版** | `/proc/self` spoof |

**关键区分**：
- **"系统路径必须能读"** 是硬约束（ART 要读 `/apex/com.android.art/lib64/libart.so`）
- **"用户真实文件不该读"** 是设计选择（可通过"导入"解决）

### 1.2 为什么"用户文件导入"比"宿主代读"简单

| 方案 | 流程 | 需要的能力 |
| --- | --- | --- |
| **宿主代读（fd 跨进程）** | guest 请求 → 主进程读 → **传 fd 回来** | AIDL + `ParcelFileDescriptor` + 生命周期管理 |
| **导入（拷贝）** | 用户选文件 → 主进程读 → **拷进沙箱** → guest 从沙箱读 | **只要一次文件拷贝** |

**导入的优势**：
- **零运行时依赖**（拷贝完就不需要任何跨进程机制）
- **guest 完全不知道文件来自哪里**（沙箱里的文件和普通文件一样）
- **可以在主进程里做**（不装 hook，天然能读真实路径）

**导入的代价**：
- 大文件要占两份空间
- 文件更新需要重新导入

**用户说"而且通常不需要这样做"** —— 确实：绝大多数 guest 只需要网络 + 自己的沙箱。

---

## 二、hook 手段的必要性重估

### 2.1 现状：21 个符号，其中 5 个只打日志

```cpp
// native-hook.cpp:1593-1610 的 hook 表
{"open", ...}, {"openat", ...}, {"access", ...}, {"stat", ...}, {"lstat", ...},
{"readlink", ...}, {"fopen", ...}, {"__system_property_get", ...},
{"open64", ...}, {"openat64", ...}, {"fopen64", ...},
{"dlopen", ...}, {"android_dlopen_ext", ...}, {"dl_iterate_phdr", ...},
{"ptrace", ...}, {"syscall", ...}, {"fork", ...}, {"opendir", ...},
{"mmap", ...}, {"mprotect", ...}, {"connect", ...}, {"getaddrinfo", ...}
```

### 2.2 按"必要性"重新分级（在"主原生/guest 全 hook"设计下）

| 级别 | 函数 | 必要性 | 理由 | 层次 |
| --- | --- | --- | --- | --- |
| **A. 必需** | `open` | ⭐⭐⭐ | 路径重定向的**唯一入口**，所有文件访问最终都到这里 | L5/L6 |
| **A. 必需** | `openat` | ⭐⭐⭐ | 同上（现代代码大量用 `openat`） | L5/L6 |
| **A. 必需** | `open64` / `openat64` | ⭐⭐⭐ | 大文件版本，同一函数体 | L5/L6 |
| **A. 必需** | `stat` / `lstat` | ⭐⭐⭐ | 检测面最常查（`P16`/`P15`/`P26`） | L5/L6 |
| **A. 必需** | `access` | ⭐⭐⭐ | `File.exists()` 的底层 | L5/L6 |
| **A. 必需** | `__system_property_get` | ⭐⭐⭐ | `getprop` 的 native 入口（`P17`） | L5 |
| **A. 必需** | **`opendir`**（**改造**） | ⭐⭐⭐ | 目录遍历：①隐藏容器 ②沙箱视图一致（`readdir` 过滤） | L5/L6 |
| **B. 有用** | `fopen` / `fopen64` | ⭐⭐ | stdio 路径（`P28` 检验点） | L5/L6 |
| **B. 有用** | `readlink` | ⭐⭐ | `/proc/self/fd/N` 反查真实路径（`P21`/`P24`） | L5 |
| **B. 有用** | `readlinkat` | ⭐⭐ | 同上（现代版本） | **新增** |
| **C. 条件** | `dlopen` / `android_dlopen_ext` / `dl_iterate_phdr` | ⭐ | 只在需要"改加载行为"或"隐藏库列表"时装 | L5 |
| **D. 待定** | `ptrace` | ❓ | 反调试伪装？现在只打日志 | L5 |
| **D. 待定** | `connect` / `getaddrinfo` | ❓ | 网络重定向，与路径无关 | L5 |
| **D. 待确认** | `syscall` | ❇ | **纯诊断**（DIAG 日志，能定位是谁在调），未参与拦截 | L5 |
| **D. 待确认** | `fork` | ❇ | 纯诊断 | L5 |
| **D. 待确认** | `mmap` / `mprotect` | ❇ | **用途待确认**（可能与 maps 伪造有关） | L5 |

### 2.3 分级后的清单（21 → 14）

**保留 14 个**：`open`、`openat`、`open64`、`openat64`、`stat`、`lstat`、`access`、`fopen`、`fopen64`、`opendir`、`readlink`、`readlinkat`（新增）、`__system_property_get`、以及条件性的 `dlopen`/`android_dlopen_ext`/`dl_iterate_phdr`/`ptrace`/`connect`/`getaddrinfo`

**保留但标记为「待确认」：`syscall`、`fork`、`mmap`/`mprotect`（不删除，改为可配置开关 `NS_HOOK_DIAG_ONLY`）

**新增 1 个**：`readlinkat`（现代代码用 `readlinkat` 多于 `readlink`）

### 2.4 为什么删掉 `syscall` 不影响功能

**关键**：`syscall(SYS_openat, ...)` 直接调系统调用，**绕过 libc 的 `openat`**。
**但**：
- `syscall` 的 GOT hook **只打日志**，本来就拦不住
- **真正需要的是"数据源伪造"**（`/proc/self/maps` 的内容），而不是"拦 syscall"
- 而 **`syscall` 读 `/proc/self/maps` 时，仍然要先 `open`/`openat`** —— **那一步被 hook 了**
- 所以：**删掉 `syscall` 的 hook，不会减少任何实际覆盖**，只是少一处 GOT 痕迹

**唯一的例外**：`syscall(SYS_openat, ...)` 直接调 —— 但这种情况**必须先有 `syscall` 的地址**（通过 `dlsym` 或直接引用），而 **`dlsym("syscall")` 返回的地址是真实的**（不经过 GOT）→ **永远拦不住**。

**所以 `syscall` 的 hook 是纯浪费**。

---

## 三、层次位置：每个 hook 手段落在哪一层

### 3.1 完整层次图（"主原生 / guest 全 hook"设计下）

```
┌─ 主进程 com.nextvm.app（不装 hook，完全原生）─────────────────┐
│  L0 进程边界：零 hook                                          │
│  L1 Java API：VirtualContext / SandboxManager（容器自己的代码）  │
│  L2 Binder：作为"服务端"响应 guest 的请求（如果需要）             │
│  L3 组件：CmdReceiver（只做转发）、UI                            │
│  【真实路径读写】：installApp / 导入 / collectProbeOutput         │
└───────────────────────────────────────────────────────────────┘
                              │ 拷贝（导入）
                              ▼
┌─ guest 进程 :p0–:p9（全 hook）────────────────────────────────┐
│  L4 Java 文件 API：⚠️ 空壳（hookFileClass 只打日志）             │
│  L5 native GOT（已加载库）：                                    │
│       A 级：open/openat/stat/lstat/access/__system_property_get  │
│             opendir（改造为 readdir 过滤）                       │
│       B 级：fopen/readlink/readlinkat                            │
│       C 级：dlopen/dl_iterate_phdr（条件）                       │
│  L6 native GOT（运行期加载的库）：❌ 链断 → 需 R1/R3             │
│  L7 直接 syscall：❌ 只打日志（不需要）                          │
│  L8 isolated 进程（WebView renderer）：❌ 进不去 → 宿主代读       │
│  L9 继承的 fd：❌ 不可能                                        │
│  L10 DAC：❌ 同 uid                                             │
└───────────────────────────────────────────────────────────────┘
```

### 3.2 逐层职责

| 层 | 主进程 | guest 进程 |
| --- | --- | --- |
| **L0 进程边界** | **零 hook**（P0-3） | 全 hook |
| **L1 Java API** | `VirtualContext` 用于自身 | `VirtualContext` 用于 guest |
| **L2 Binder** | 服务端（如果 guest 需要） | proxy（7 个 `*Proxy.kt`） |
| **L3 组件** | `CmdReceiver` 转发、UI | `ActivityThreadHook` |
| **L4 Java 文件** | 不需要 | ⚠️ 空壳（可补但收益有限） |
| **L5 native GOT** | **不需要** | ✅ A+B 级 11 个符号 |
| **L6 新加载库** | 不需要 | ❌ 需 R1（patch `libnativeloader`） |
| **L7 syscall** | 不需要 | ❌ 不需要（见 §2.4） |
| **L8 isolated** | **代读**（唯一能做的地方） | 进不去 |
| **L9 fd** | — | — |
| **L10 DAC** | — | — |

---

## 四、WebView 代读：为什么必须由主进程做，代价多大

### 4.1 为什么 guest 进程做不了

```
WebView 的 renderer 在 isolated 进程（sandboxed_process0，独立 uid）
  → 它读 file:///sdcard/x.html
    → 按自己的 uid 判权限
      → 读不到 guest 的沙箱（沙箱属 guest uid）
        → 只能读真实 /sdcard（但那是真实的，且可能被 DAC 拒）
```

**所以**：
- **renderer 读不到沙箱**（uid 不同）
- **renderer 读真实路径**会读到真实文件（不是我们要的沙箱版本）或失败

### 4.2 代读的两个可能位置

| 位置 | 能做什么 | 问题 |
| --- | --- | --- |
| **guest 进程的 `shouldInterceptRequest`** | 拦截 URL → 读沙箱 → 返回 `WebResourceResponse(InputStream)` | ⚠️ **主页面（`loadUrl`）可能不走它** |
| **主进程** | 读真实文件 | ❌ 无法注入 `WebViewClient`（WebView 在 guest 进程） |

**所以实际是**：**guest 进程拦截 + 读沙箱**，主进程只负责"把真实文件导入沙箱"。

**修正之前的说法**：
> **不需要"主进程代读 + fd 跨进程"** —— 因为 **guest 进程能读自己的沙箱**，
> 只要文件在沙箱里，`shouldInterceptRequest` 就能读到。

### 4.3 代价估计

| 项 | 内容 | 工作量 |
| --- | --- | --- |
| 1 | guest 进程 hook `WebView.setWebViewClient`（或 `WebViewClient.shouldInterceptRequest`） | 2–3 天（需要 hook framework 类，难） |
| 2 | 在 guest 进程里读沙箱 → 构造 `WebResourceResponse` | 0.5 天 |
| 3 | 处理 MIME / 编码 / 相对路径 / 同源 | 1 天 |
| 4 | 验证覆盖范围（主页面 / iframe / XHR / CSS / JS） | 1 天 |
| **合计** | | **4–5 天** |

**关键风险**：
- **`shouldInterceptRequest` 对主页面（`loadUrl("file://...")`）是否触发** —— **不确定**，需要先验证
- 如果主页面不走它 → 只能改 `loadUrl` 的参数（但那要 hook `WebView` 类，更难）

### 4.4 一个更简单的替代：**把 HTML 直接写进沙箱，让 guest 用相对路径**

```
主进程（不装 hook）：
  把 /sdcard/Download/x.html 拷到 <沙箱>/sdcard/x.html

guest：
  WebView.loadUrl("file://" + 自己的 externalFilesDir + "/x.html")
    → renderer 读沙箱的绝对路径 → 仍然读不到（uid 不同）
```

**不行** —— renderer 的 uid 问题无法绕开。

**除非**：把文件放在 **renderer 能读的地方**（如 `/data/local/tmp`，644）→ 但那等于放弃隔离。

**所以 WebView 的 `file://` 在本架构下真的很难**：
- renderer 读不到沙箱（uid）
- 代读要 hook `WebView` 类（framework 类，难）
- **可能应该接受这个限制**（P1-3 降级或放弃）

---

## 五、修正后的架构总览

### 5.1 三个进程（修正：不需要 `:probe` 了？）

| 进程 | 装 hook | 职责 |
| --- | --- | --- |
| **主进程** | ❌ 零 hook | UI / 导入（读真实文件→写沙箱）/ `CmdReceiver` 转发 / `collectProbeOutput` |
| **guest 进程** `:p0`–`:p9` | ✅ 全 hook | 跑 guest；所有路径都在沙箱内 |
| **`:probe` 进程** | ✅ 装 hook | ⚠️ **是否还需要？** 见 §5.2 |

### 5.2 `:probe` 进程是否必要（重新评估）

**用户之前的意图**：测试要跑在 hook 的进程里（模拟 guest 环境）。

**但现在**：
- **guest 进程本身就是"全 hook"环境** → **直接跑在 guest 进程里就行**
- **`:probe` 的唯一价值**：不加载 guest 的 ClassLoader，跑纯容器测试

**判断**：
- 如果测试是"测容器自身的行为"→ `:probe` 有用
- 如果测试是"测 guest 环境"→ 直接跑 guest 进程

**建议**：**暂不新增 `:probe`**（避免多一个进程的复杂度），测试走"探针在 guest 进程里跑"（现状就是这么做的）。

### 5.3 数据流（简化版）

```
[导入 APK]
adb → CmdReceiver（主进程，零 hook）
      → installApp 直接读真实 /sdcard/Download/x.apk ✅（不需要任何豁免）
        → 拷贝到 <沙箱>/apks/<instance>.apk

[导入用户文件（新增功能，可选）]
用户选文件 → 主进程读 → 拷到 <沙箱>/sdcard/import/<name>
  → guest 从沙箱读（与普通文件无区别）

[启动 guest]
stub Activity（:p0）→ ActivityThreadHook → initNativeHooks（全 hook）
  → 所有路径访问都在沙箱内

[跑探针]
adb → CmdReceiver（主进程）→ 转发到 guest 进程（stub Service）
  → :p0 进程（全 hook）跑测试 → 结果走 logcat + 文件

[WebView]
  限制：renderer 读不到沙箱 → 需要 hook WebView 类（难）→ 可能放弃
```

---

## 六、修正后的 hook 清单（最终版）

| # | 符号 | 级别 | 层次 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | `open` | A | L5 | ✅ 已做 |
| 2 | `openat` | A | L5 | ✅ 已做 |
| 3 | `open64` | A | L5 | ✅ 已做 |
| 4 | `openat64` | A | L5 | ✅ 已做 |
| 5 | `stat` | A | L5 | ✅ 已做 |
| 6 | `lstat` | A | L5 | ✅ 已做 |
| 7 | `access` | A | L5 | ✅ 已做 |
| 8 | `__system_property_get` | A | L5 | ✅ 已做 |
| 9 | **`opendir`** | A | L5 | ⚠️ **需改造**（加 `readdir` 过滤） |
| 10 | `fopen` | B | L5 | ✅ 已做 |
| 11 | `fopen64` | B | L5 | ✅ 已做 |
| 12 | `readlink` | B | L5 | ✅ 已做 |
| 13 | **`readlinkat`** | B | L5 | ❌ **建议新增** |
| 14 | `dlopen` | C | L5 | ⚠️ 条件保留 |
| 15 | `android_dlopen_ext` | C | L5 | ⚠️ 条件保留 |
| 16 | `dl_iterate_phdr` | C | L5 | ⚠️ 条件保留 |
| 17 | `ptrace` | D | L5 | ❓ 待定 |
| 18 | `connect` / `getaddrinfo` | D | L5 | ❓ 待定（网络，与路径无关） |
| ~~19~~ | ~~`syscall`~~ | ❌ | — | **删** |
| ~~20~~ | ~~`fork`~~ | ❌ | — | **删** |
| ~~21~~ | ~~`mmap` / `mprotect`~~ | ❌ | — | **删** |
| **新** | **L6（运行期加载的库）** | — | **L6** | ❌ **需 R1**（patch `libnativeloader`） |

---

## 七、结论

| 用户的问题 | 答案 |
| --- | --- |
| **guest 读取真实文件是否必要** | ❌ **不必要** —— 只要精确表述为"guest 只读系统路径 + 自己的沙箱" |
| **"主原生 + guest 全 hook"是否可行** | ✅ **可行，而且让架构简单一大截** —— 不需要 fd 跨进程 |
| **导入代替代读** | ✅ **正确** —— 一次拷贝，零运行时依赖 |
| **WebView 由主线程代读是否可行** | ⚠️ **比预想的难** —— renderer 是 isolated uid，读不到沙箱；代读要 hook `WebView` 类（framework 类）。**代价 4–5 天，且可能只能覆盖部分场景** |
| **hook 手段** | 21 → **14（+`readlinkat`）**，删 `syscall`/`fork`/`mmap`/`mprotect` |
| **层次位置** | A/B/C 级都在 L5；L6 需 R1；**L4 仍是空壳**；L7–L10 不需要/做不到 |
| **`:probe` 进程是否必要** | ⚠️ **建议暂不新增** —— guest 进程本身就是"全 hook"环境，测试直接跑那里 |
