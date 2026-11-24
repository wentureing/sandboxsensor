# 调查：能否让容器在"别的进程"启动 guest，缩小 hook 范围 + 技术选型分层

- 日期：2026-11-24
- 起因：用户提出 —— 之前提到 hook 和进程紧密相关，能否让容器在别的进程启动 guest，
  防止 hook 范围过大？统一目标应是"guest 难以发现"，而不是拦住所有调用（例如 guest 用到的外部进程）
- 结论先给：**guest 已经在独立进程里了**（`:p0`–`:p9`）；
  **但"hook 范围过大"不是进程问题，是"覆盖面"问题**；
  **真正能缩小的三条路是：按需 hook / 按函数 hook / 延迟 hook**

---

## 一、现状：进程模型已经分离（查代码确认）

### 1.1 十四个进程槽（manifest 实测）

```xml
<!-- app/src/main/AndroidManifest.xml -->
<activity android:name=".stub.P0$Standard00" android:process=":p0" ... />
<activity android:name=".stub.P1$Standard00" android:process=":p1" ... />
...
<activity android:name=".stub.P9$Standard00" android:process=":p9" ... />
```

每个槽有 5 类组件（`Standard` ×5 + `SingleTop` ×2 + `SingleTask` ×2 + `SingleInstance` ×1 + `Service` ×5 + `Provider` ×3 + `Receiver` ×2）。

```kotlin
// VirtualMultiProcessManager.kt:49
const val MAX_PROCESS_SLOTS = 10
// L78
private val slotAllocation = ConcurrentHashMap<Int, String>()   // slot -> "instanceId:processName"
```

**所以**：
- **一个 guest 的"每个逻辑进程"占一个槽**
- guest 声明 `android:process=":remote"` → 分配额外槽
- **最多 10 个同时运行的 guest 进程**

### 1.2 hook 只在两个地方装（查代码确认）

```
VirtualEngine.kt:280   nativeHookBridge.initNativeHooks(context)    ← 主进程（initialize）
VirtualEngine.kt:880   nativeHookBridge.initNativeHooks(context)    ← guest 进程（getOrCreateClassLoader）
```

**所以**：
- **容器主进程装 hook**（P0-3 计划让它别装）
- **guest 进程装 hook**（在创建 ClassLoader 时）

### 1.3 结论：进程分离**已经做了**

| 用户设想 | 现状 |
| --- | --- |
| "让容器在别的进程启动 guest" | ✅ **已经是**（`:p0`–`:p9`） |
| "防止 hook 范围过大" | ❌ **不是进程问题** —— 见第二节 |

---

## 二、为什么"换进程"解决不了 hook 范围问题

### 2.1 硬约束：路径重定向**必须在 guest 进程内**

```
guest 进程内调用 open("/sdcard/x")
  → 只有在这个进程里 hook 到 open，才能改路径
  → 在别的进程 hook 没用（那是另一个地址空间）
```

**所以**：
> **"把 hook 移到别的进程"在技术上不成立** —— 除非把 `open` 也代理出去（但那需要改 guest 的调用点，等于又要在 guest 里 hook）。

### 2.2 但"代理出去"在一个特定场景成立

**如果 guest 的调用是"经过 Binder 到系统服务"**，那么：
- 系统服务在**另一个进程**（`system_server`）
- **拦不到**（那是系统进程）

**但 guest 调 `PackageManager` 时，中间有我们的 proxy**（在 guest 进程的 Java 层）→ **能拦**。

**所以**：
- **Java 层 / Binder 层的代理**：在 guest 进程内（proxy 对象活在 guest 进程）→ 已做
- **native 层的路径重定向**：必须在 guest 进程内 → 无解
- **WebView renderer（isolated 进程）**：**完全进不去** → 只能靠宿主代读

### 2.3 "hook 范围过大"的真实含义

用户说的"范围"可能指三件事，要分开看：

| 可能的"范围" | 现在的量级 | 能不能缩小 |
| --- | --- | --- |
| **A. 装 hook 的进程数** | 主进程 + N 个 guest 进程 | ✅ **能**：主进程不装（P0-3） |
| **B. 每个进程里 hook 的库数** | 所有 `/data/app/`、`/data/user/`、`/virtual/` 的库 | ✅ **能**：按需 patch |
| **C. 每个库里 hook 的函数数** | 21 个符号（`open`/`openat`/`access`/`stat`/`lstat`/`readlink`/`fopen`/`open64`/`openat64`/`fopen64`/`dlopen`/`android_dlopen_ext`/`dl_iterate_phdr`/`ptrace`/`syscall`/`fork`/`opendir`/`mmap`/`mprotect`/`connect`/`getaddrinfo`/`__system_property_get`） | ✅ **能**：按需 hook |

**"guest 难以发现"的目标下，A/B/C 都值得缩小** —— 因为**每个 hook 点都是"可被发现的痕迹"**。

---

## 三、guest 能"发现 hook"的具体途径（这是缩小的依据）

| 发现途径 | 能看到什么 | 缩小哪一项 |
| --- | --- | --- |
| **`/proc/self/maps`** | 我们的库（`libnextvm.so`）被加载 → 一眼看出 | **B**（少 patch 就少改，但库还是会加载） |
| **GOT 内容对比** | 某个库的 GOT 项指向非 libc 地址 → 被 hook | **B/C**（少 hook 就少暴露） |
| **`.text` 校验和** | inline hook 会改函数体 → 校验失败 | **R2 特有** |
| **调用耗时** | hook 后的函数慢一点 | A/B/C 都影响 |
| **`/proc/self/task/*/stat`** | hook 线程存在 | A |
| **行为异常**（如 `stat` 返回 `ENOENT` 但 `readdir` 能看到） | **假代理不一致** | 不是"范围"问题，是一致性问题 |

**结论**：
> **每多 hook 一个库/函数，就多一处"可被发现"的面。**
> **所以"按需 hook"不是性能优化，是**隐蔽性优化**。

---

## 四、三条"缩小范围"的具体做法

### 4.1 缩小 A：主进程不装 hook（P0-3）

| 项 | 内容 |
| --- | --- |
| 现状 | 主进程在 `VirtualEngine.initialize()` 里装 hook |
| 改法 | `initNativeHooks` 开头判断 `getProcessName() == packageName` → 直接 return |
| 收益 | 主进程的 maps/GOT 都干净；容器自己的 `open` 不再被拦（不需要豁免） |
| 风险 | 主进程若依赖 hook 的功能（如 `AntiDetectionEngine.hidePath`）会失效 |

### 4.2 缩小 B：按需 patch 库（不是所有 app 库）

| 现状 | 建议 |
| --- | --- |
| `is_app_lib`：`/data/app/`、`/data/user/`、`/virtual/` **全部 patch** | 只 patch **guest 自己的库**（`/data/app/<guest-pkg>-*/`），不 patch 其他应用 |
| 理由 | 容器进程里可能加载了别的库（如系统 webview），patch 它们没意义还增加暴露面 |

**实现**：`is_app_lib` 加上 `g_spoofed_package_name` 的过滤：

```cpp
// 现状：只看路径前缀
if (strstr(lib_name, "/data/app/") != nullptr) return true;

// 建议：还要看包名匹配
if (strstr(lib_name, "/data/app/") != nullptr) {
    return strstr(lib_name, g_spoofed_package_name.c_str()) != nullptr;
}
```

### 4.3 缩小 C：按需 hook 函数（21 个 → 分层）

| 层 | 函数 | 是否必需 | 理由 |
| --- | --- | --- | --- |
| **必需** | `open` / `openat` / `open64` / `openat64` | ✅ | 所有文件访问的入口 |
| **必需** | `stat` / `lstat` / `access` | ✅ | 检测面最常查的 |
| **必需** | `__system_property_get` | ✅ | `getprop` 的 native 入口 |
| **有用** | `fopen` / `fopen64` | ✅ | stdio 路径 |
| **有用** | `readlink` | ✅ | fd/路径反查 |
| **有用** | `opendir` | ✅ | 目录遍历（隐藏容器必须） |
| **有条件** | `dlopen` / `android_dlopen_ext` / `dl_iterate_phdr` | ⚠️ | 只在需要"改加载行为"时装 |
| **可疑** | **`syscall`** | ❌ **建议去掉** | 只打日志、开销大、且是"被发现的痕迹"（GOT 有改动） |
| **可疑** | **`fork`** | ❌ 建议去掉 | 只打日志 |
| **可疑** | **`ptrace`** | ⚠️ | 只用于反调试伪装？需确认 |
| **可疑** | **`mmap` / `mprotect`** | ⚠️ | 用途不明；`mprotect` hook 尤其危险 |
| **可疑** | **`connect` / `getaddrinfo`** | ⚠️ | 网络重定向，与路径无关；每多一处就多一处暴露 |

**核心判断**：
> **只打日志的 hook 应该删掉** —— 它们不改变行为，只增加"GOT 被改"的痕迹。

**这是一个可以立即做的收益**：把 `syscall`/`fork`/`opendir`（只打日志的部分）从 hook 表里去掉，减少 3 个暴露点。

---

## 五、技术选型：分层次完整描述

### 层次总览

| 层 | 目标 | 手段 | 是否已在做 |
| --- | --- | --- | --- |
| **L0 进程边界** | 减少"装 hook 的进程" | 主进程不装 + guest 独立进程 | 部分（guest 已独立，主进程待改） |
| **L1 Java/Binder 代理** | 应用层查询造假 | `PackageManagerProxy` / `ActivityManagerProxy` / `ContentResolverProxy` | ✅ 已做（7000+ 行） |
| **L2 native 路径重定向** | 文件访问改路径 | GOT patch（R0）/ patch libnativeloader（R1）/ inline hook libc（R2） | R0 已做，L6 断链 |
| **L3 内容伪造** | `/proc/self/*` 返回假内容 | memfd | ⚠️ 有框架（`make_proc_spoof_fd`） |
| **L4 拒绝访问** | 让容器"不存在" | `nativeHidePath` + `readdir` 过滤 | ⚠️ 精确匹配，需改前缀 |

### 选型 1：L0 进程边界

| 项 | 内容 |
| --- | --- |
| **目标** | 容器主进程不装 hook → 主进程的 maps/GOT 完全干净 |
| **做法** | `initNativeHooks` 开头：`if (getProcessName() == context.packageName) return` |
| **前置检查** | `CmdReceiver` 是否只在主进程；`AntiDetectionEngine.hidePath` 等调用方是否跨进程 |
| **收益** | 主进程"零痕迹"；容器自己的路径访问不需要任何豁免 |
| **风险** | 中（主进程若依赖 hook 功能会失效） |
| **工作量** | 1 天 |
| **验证** | 主进程 maps 里没有 `libnextvm`；`COLLECT_OUT` 等命令仍可用 |
| **优先级** | **P0-3（已在计划里）** |

### 选型 2：L1 Java/Binder 代理（已做，需补强）

| 项 | 内容 |
| --- | --- |
| **目标** | 应用层"查询"类调用全部造假（OWASP 的 package structure / data directory） |
| **做法** | 在现有 7 个 proxy 里补容器过滤 |
| **缺口** | `P13` 容器包可见、`P16` 容器 dataDir 可 stat |
| **收益** | 消灭 OWASP 点名的两类检测面 |
| **风险** | 中（容器自己可能查自己） |
| **工作量** | `P13` 0.5 天 + `P16` 1 天 |
| **验证** | 探针 `P13`/`P16` |
| **优先级** | **P0-4 + P0-5（已在计划里）** |

### 选型 3：L2 native 路径重定向

三条子路线，详见 `计划-提升路线图.md` §0.5：

| 子路线 | 机制 | 覆盖 | 波及 | 过滤难度 | 风险 | 工作量 |
| --- | --- | --- | --- | --- | --- | --- |
| **R0**（现状） | GOT patch 各库 | 已 patch 的库 | 小 | 🟢 无 | 低 | — |
| **R1** | patch `libnativeloader` | 新加载库 | 中 | 🟢 低 | 中 | 1.5 天 |
| **R2** | inline hook `libc` | 所有调用者 | **大** | 🔴 高 | 中高 | 3–4 天 |
| **R3** | shadowhook 只 hook `dlopen` | 新加载库 | 小 | 🟢 无 | 中 | 2 天 |

**推荐**：**R3**（爆炸半径最小）；不想引第三方则 **R1**；要对付加固壳才用 **R2**。

### 选型 4：L3 内容伪造（`/proc/self/*`）

| 项 | 内容 |
| --- | --- |
| **目标** | `/proc/self/maps` 不出现容器路径/库名（现在 `pkg_hit=8`，Gaia `hostHits=0`） |
| **做法** | `open("/proc/self/maps")` 返回 memfd（内容是过滤后的副本） |
| **难点** | ①maps 动态变化 ②`mmap` 绕过 `read` ③memfd 的 `readlink` 会露 |
| **收益** | 消灭"known frameworks"检测面 |
| **风险** | 高 |
| **工作量** | 2–3 天 |
| **优先级** | P1（或逆向 Gaia 后做） |

### 选型 5：L4 拒绝访问（"假装不存在"）

| 项 | 内容 |
| --- | --- |
| **目标** | guest 查不到容器（`stat`/`access`/`opendir` 一致地返回"不存在"） |
| **做法** | ①`is_path_hidden` 改**前缀匹配** ②加容器两个前缀 ③**`readdir` 过滤** |
| **难点** | 包装 `DIR*`，维护 `fd → 过滤器` 映射；不能藏掉容器自己需要的 |
| **收益** | 消灭"宿主包可见"，且**无法被区分**（这是最强伪造） |
| **风险** | **高**（容易把容器自己搞坏） |
| **工作量** | 1 天 |
| **优先级** | **P0-5** |

---

## 六、按"guest 难以发现"重新排序的目标

用户明确：**统一目标是"guest 难以发现"，而不是拦住所有调用**。

按这个目标重排：

| 优先级 | 任务 | 为什么能"减少被发现" |
| --- | --- | --- |
| **1** | **删掉只打日志的 hook**（`syscall`/`fork`） | 每删一个，GOT 就少一处改动 → 少一个痕迹 |
| **2** | **`is_app_lib` 加包名匹配**（缩小 B） | 少 patch 无关库 → maps/GOT 更干净 |
| **3** | **P0-4/P0-5**（容器包不可见 + dataDir 不可访问） | 直接骗过 OWASP 点名的两类检测 |
| **4** | **P0-3**（主进程不装 hook） | 主进程零痕迹 |
| **5** | R3/R1（修 L6） | 让路径重定向对**所有**库生效 —— 但这是"拦住所有调用"，**优先级低于"减少痕迹"** |
| **6** | L4 maps 伪造 | 消灭最硬的一类检测（难） |

**关键洞察**：
> **"拦住所有调用"和"减少被发现"在某些情况下是冲突的。**
> 每多 hook 一个函数，就多一处 GOT 改动 → **更容易被发现**。
> 所以**不是"hook 得越全越好"**，而是**"只 hook 必须的，且让假代理保持一致"**。

---

## 七、结论

| 问题 | 答案 |
| --- | --- |
| **能否让容器在别的进程启动 guest？** | **已经是了**（`:p0`–`:p9`，10 槽） |
| **换进程能缩小 hook 范围吗？** | **不能** —— 路径重定向**必须在 guest 进程内**（`open` 在那里调用） |
| **"hook 范围过大"到底是什么问题？** | **A 装 hook 的进程数 / B patch 的库数 / C hook 的函数数** —— 三者都可缩小 |
| **缩小范围有什么收益？** | **每个 hook 点都是一处"可被发现的痕迹"** → 缩小 = 提高隐蔽性 |
| **应优先做什么？** | ①删只打日志的 hook ②`is_app_lib` 加包名匹配 ③`P13`/`P16` ④`P0-3` |
| **L6 要不要修？** | 要，但**优先级低于"减少痕迹"** —— 它是"拦住更多调用"，不是"减少被发现" |
