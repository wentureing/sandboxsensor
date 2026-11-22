# Hook 作用域分析：为什么原设计会拦系统渲染库，收窄后会不会导致路径泄漏

- 日期：2026-11-22
- 背景：`p13` 把 `native-hook.cpp` 的 GOT patch 收窄为"只 patch 应用库"（跳过 `/vendor` `/system` `/apex`）
- 关联：`root-cause-native-hook-pollution-20261122.md`

---

## 1. 原设计为什么会去 patch 系统渲染库

### 代码层面

`native-hook.cpp` 的 late-patch 流程（`Hooked dlopen()` → `late_patch_loaded_libraries()`）：

```
dlopen/android_dlopen_ext 被 hook
  → 每次成功加载新库，遍历该库的 .rela.plt
  → 对每个符号与 g_hook_entries 匹配（open/openat/stat/fopen/__system_property_get/connect…）
  → 命中就 patch 该库的 GOT 槽位
```

**关键：原逻辑对 `lib_name` 没有任何判断**——只要库被加载、只要里面有匹配的导入符号，就 patch。

### 设计意图（为什么这么做）

| 理由 | 说明 |
| --- | --- |
| **最大化拦截覆盖率** | 目标应用可能不直接调 `open`，而是通过某个第三方/系统库间接调用。若只 patch 应用自己的库，这些间接调用会漏掉 |
| **不需要知道谁在调** | GOT patch 是最"无脑"的方案：不区分调用方，谁加载了就 patch 谁 |
| **实现简单、无配置** | 不需要维护"哪些库该 patch"的名单 |

### 代价（本次踩到的）

`/vendor/lib64/egl/libGLESv2_adreno.so`、`eglSubDriverAndroid.so` 也被 patch：

```
Hooked stat in /vendor/lib64/egl/libGLESv2_adreno.so
Hooked fopen in /vendor/lib64/egl/libGLESv1_CM_adreno.so
Hooked __system_property_get in .../eglSubDriverAndroid.so
```

GPU 驱动在**编译 shader** 时要用 `stat` 找缓存、`fopen` 读缓存、读属性判断能力。这些调用被换成容器的路径重定向实现，驱动拿到异常结果 → `abort()`。

> 这就是"为了覆盖率牺牲正确性"的典型案例：**patch 一个你不理解的库，就是在改它的运行前提。**

---

## 2. 收窄后会不会泄漏？（分场景）

先明确一件事：**目标应用的直接调用不会漏。**

```
目标应用的 .so（沙箱路径 /data/user/.../virtual/... 或 /data/app/...）
  → 它的 .rela.plt 里就有 open/stat 的 GOT 槽位
  → p13 仍然 patch 它 ✔
  → 应用代码调 open() 时进入容器 hook ✔
```

真正会漏的是**「应用 → 第三方/系统库 → 路径 API」这种间接调用**：

| 场景 | 是否漏 | 危害 |
| --- | --- | --- |
| 应用自己 native 库直接 `open("/data/data/xxx")` | ❌ 不漏 | — |
| 应用 → `libsqlite.so` → `open(db路径)` | ⚠️ **漏** | SQLite 可能读到真实路径 |
| 应用 → `libmediandk.so` → `open(媒体文件)` | ⚠️ **漏** | 媒体路径泄漏 |
| 应用 → `libwebviewchromium.so` → `open(缓存路径)` | ⚠️ **漏** | WebView 缓存落真实路径 |
| 应用 → `libGLESv2_adreno.so` → `stat/fopen` | ⚠️ 漏，但**无害** | 那是 GPU 缓存/驱动路径，不是应用私有路径 |
| 应用 → `libc.so` → `open` | ❌ 不漏 | 应用库的 GOT 已指向 hook，调用在进 libc 前就被截住 |

**结论**：

1. **对"应用私有数据路径"的拦截，收窄后仍然覆盖大部分**（应用自己调用的部分）
2. **风险集中在"系统库代应用访问文件"**：SQLite、MediaCodec、WebView
3. **对 GPU 驱动的"漏"完全无害**——它访问的是 `/vendor`、`/data/vendor/gpu`、驱动缓存，本来就不该被重定向

---

## 3. 三种策略对比

| 策略 | 覆盖率 | 稳定性 | 说明 |
| --- | --- | --- | --- |
| **全量 patch**（原版） | 最高 | **差**（污染 GPU 驱动 → 崩溃） | 本次问题的来源 |
| **白名单：只 patch 应用库**（p13 当前） | 中高 | 好 | 简单、可解释；系统库代访问会漏 |
| **黑名单：patch 所有库，但排除图形栈** | **高** | 较好 | 只排除 `libGLES*`/`libEGL*`/`libvulkan*`/`eglSubDriver*`/`libllvm-glnext`/`vulkan.adreno` |

**推荐路径**：

```
第 1 步（当前）：白名单 —— 先确认"崩溃真的消失"
    这是最小风险的一步，且能立刻验证根因判断对不对

第 2 步：用探针量化泄漏
    在容器内跑探针的 N03/N07（native open 字面量路径），看返回的是
    沙箱路径还是真实路径

第 3 步：如果泄漏影响目标应用
    改为黑名单（只排除图形栈），把 SQLite/WebView 等系统库重新纳入
    注意：每纳入一个系统库，都要单独验证它不会因 hook 而异常
```

---

## 4. 怎么验证"有没有泄漏"（可执行）

用现有探针就能测，重点看这几条：

| 探针项 | 期望（无泄漏） | 泄漏表现 |
| --- | --- | --- |
| `N03.open_literal`（native `open("/data/data/<pkg>/files/...")`） | 成功且落沙箱 | 失败（ENOENT）或指向真实路径 |
| `N08.readlink_fd`（`/proc/self/fd/N`） | 沙箱路径 | 真实路径 |
| `J26/J27`（Java 字面量路径） | 走 Java 层，不受 hook 影响 | — |
| `N13/N14`（`/data/user/0` 前缀） | 沙箱路径 | 真实路径 |

**注意**：p13 是从 fork 初始版本回退的，**不包含 v1.1 探针的 Provider 入口**（那是探针侧的改动，与容器无关，探针 APK 仍是 v1.1）。

如果 `N03` 显示真实宿主路径 → 说明该库（`libprobe.so`）的 GOT 没被 patch。但按 p13 的白名单，`libprobe.so` 在沙箱 `/data/user/.../virtual/...` 下 → **应该被 patch**。所以预期 `N03` 是沙箱路径。

---

## 5. 一个更根本的观察

**"要不要 patch 系统库"这个问题，本质上是在问"容器的威胁模型是哪个"**：

| 威胁模型 | 需要 patch 的范围 |
| --- | --- |
| 只要目标应用**功能正常**（数据隔离） | 应用库足够 |
| 要防**目标应用察觉**（检测对抗） | 需要更广（包括系统库代访问、`/proc`、内联 svc） |

按 `01` 决策表，本项目默认走 **B1（标准路径）但以功能正确为优先**，所以**白名单起步是对的**；等目标应用清单确定后，再针对具体泄漏点（SQLite / WebView）做定点收窄。

---

## 6. 一句话结论

> **原设计 patch 所有库是为了"不漏掉间接调用"，代价是改坏了 GPU 驱动的运行前提；收窄到应用库后，直接调用不漏，只有"系统库代应用访问文件"这类场景会漏（SQLite / MediaCodec / WebView）。**
> 这类泄漏用探针的 `N03`/`N08` 就能量化；若确认影响目标应用，再改用"黑名单（只排除图形栈）"找回覆盖率，而不是回到全量 patch。
