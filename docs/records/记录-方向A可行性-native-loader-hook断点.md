# 方向 A 可行性：让运行期 `System.loadLibrary` 加载的库也能被 patch

- 日期：2026-11-23
- 起因：P28 实测发现 `libprobe.so` 的 GOT 完全没被 patch（见 `记录-P28实测推翻越权修复前提.md`）
- 结论：**可行，且改动比预想小**；但有一个必须先验证的前提

---

## 一、问题重述

容器 `nativeInit()` 只 patch **当时已加载**的库的 GOT。之后新加载的库靠 `hooked_dlopen` 触发 `patch_newly_loaded_libraries()`。

**实测证明这条链断了**：
- 探针 `libprobe.so`（`System.loadLibrary("probe")` 加载）的 `fopen`/`open` 全部是原始 libc
- `P28` 用 `/sdcard/Download/probe-wtest.txt` 做判据（真实存在、沙箱不存在）→ **成功写真实文件** = 没被重定向

---

## 二、官方调用链（已核实）

来源：[Android so加载流程分析](https://www.cnblogs.com/wsy1qaz2wsx/p/19021579)（对照 AOSP Android 13 源码）

```
System.loadLibrary / System.load
  → Runtime.loadLibrary0 / Runtime.load0
    → nativeLoad (JNI)
      → Runtime_nativeLoad            (libcore/ojluni/src/main/native/Runtime.c)
        → JVM_NativeLoad              (art/runtime/jni/)
          → JavaVM::LoadNativeLibrary (art/runtime/native/java_lang_Runtime.cc)
            → OpenNativeLibrary
              ├─ 系统类库 → android_dlopen_ext
              └─ 应用 so   → OpenNativeLibraryInNamespace
                              → NativeLoaderNamespace::Load
                                 (art/libnativeloader/native_loader_namespace.cpp)
                                → android_dlopen_ext   ← 最终调用点
```

**三个关键事实**：

| 事实 | 意义 |
| --- | --- |
| 应用 so 走 `OpenNativeLibraryInNamespace` | 不是简单的 `dlopen`，是**命名空间感知**的加载 |
| 最终都调 `android_dlopen_ext` | 容器**已经有** `hooked_android_dlopen_ext`，只是没被调用 |
| 调用方是 **`libnativeloader.so`**（`/apex/com.android.art/lib64/`） | 它属于**系统库**，当前过滤链**不 patch** → 调用绕过了 hook |

**所以"链断"的具体位置**：
```
libnativeloader.so 的 android_dlopen_ext 调用
  → 走它自己的 GOT
    → 但 libnativeloader.so 没被 patch（/apex/ 属系统库）
      → 直接进 real android_dlopen_ext
        → 容器完全不知道这次加载
```

---

## 二点五、官方源码验证（2026-11-23 23:30）

用户要求"利用谷歌文档验证信息"。以下是从 AOSP 官方源码直接抓到的内容（非第三方博客）。

### 已验证 1：ndroid_dlopen_ext 是 bionic 的公开 API

来源：ndroid.googlesource.com/platform/bionic/+/refs/heads/main/libdl/libdl.cpp

`cpp
// L135
void* android_dlopen_ext(const char* filename, int flag, const android_dlextinfo* extinfo) {
    const void* caller_addr = __builtin_return_address(0);
    return __loader_android_dlopen_ext(filename, flag, extinfo, caller_addr);  // L137
}
`

**意义**：确认它是一个**可被 GOT patch 的普通导出符号**（不是内联宏、不是 direct syscall），
所以只要调用方库的 GOT 被 patch，就能拦到。

### 已验证 2：libnativeloader 的存在与命名空间机制

来源：ndroid.googlesource.com/platform/art/+/refs/heads/main/libnativeloader/native_loader_namespace.cpp

关键片段（原文行号）：

`
L55: android_namespace_t* raw = android_get_exported_namespace(name.c_str());
L60: native_bridge_namespace_t* raw = NativeBridgeGetExportedNamespace(name.c_str());
L65: return Errorf("namespace {} does not exist or exported", name);
L68: // The system namespace is called "default" for binaries in /system and
L82: // nullptr also means default namespace to the linker.
`

**意义**：应用 so 的加载**确实走命名空间**（NativeLoaderNamespace），
与"系统类库直接 ndroid_dlopen_ext"是两条路 —— 印证了第三方博客的调用链描述。

### 未验证到（网络受限）

| 目标 | 状态 |
| --- | --- |
| rt/libnativeloader/native_loader.cpp（ndroid_dlopen_ext 的实际调用点） | 多次重试均 HTTP NaN（代理不稳） |
| rt/runtime/native/java_lang_Runtime.cc | 404（路径可能已变） |

**所以"NativeLoaderNamespace::Load → ndroid_dlopen_ext"这一环，目前只有第三方来源支持，未拿到官方源码行。**

### 结论

| 结论 | 证据等级 |
| --- | --- |
| ndroid_dlopen_ext 是普通导出符号、可 GOT patch | **官方源码确认** |
| 应用 so 走命名空间加载 | **官方源码确认** |
| 命名空间加载最终调 ndroid_dlopen_ext | **第三方来源，官方未取到** |
| System.loadLibrary 的调用方是 libnativeloader.so | **第三方来源** |

**因此阶段 1（打日志看 libnativeloader.so 在不在加载列表）不能省** ——
它会把最后两个"未验证"直接变成实测事实。

## 三、可行性评估

### 3.1 最小改动方案

**只需要让 `libnativeloader.so` 进入 patch 列表**：

```cpp
// native-hook.cpp 的过滤链，第 3 步附近加
// 3.5) ART 的 native loader：必须 patch，否则 System.loadLibrary 加载的应用库
//      拿不到 GOT patch（实测见 docs/records/记录-P28实测推翻越权修复前提.md）
const bool is_native_loader = strstr(lib_name, "libnativeloader") != nullptr;
if (is_app_lib || is_native_loader) { /* fallthrough: patch */ }
```

**为什么改动这么小**：
- `hooked_android_dlopen_ext` **已经存在**（容器有）
- `patch_newly_loaded_libraries` **已经存在**
- 只差"让调用走到它们"

### 3.2 需要验证的前提（**必须先做**）

| # | 要验证 | 方法 | 风险 |
| --- | --- | --- | --- |
| 1 | `libnativeloader.so` 在 `nativeInit` 时**是否已加载** | `dl_iterate_phdr` 打印当前库列表，看有没有它 | 低 |
| 2 | 它的 `android_dlopen_ext` GOT 项**是否存在** | patch 日志里看是否有 `patch android_dlopen_ext in libnativeloader` | 低 |
| 3 | patch 后 **ART 是否仍能正常加载库** | 装 guest 跑一遍，看有没有崩 | **中**（ART 核心组件） |

**前提 1 是关键**：如果 `libnativeloader.so` 在 `nativeInit` **之后**才加载，那 `nativeInit` 时扫不到它 → 还需要别的办法（比如 patch `System.loadLibrary` 的 Java 层，或在自己的 `dlopen` 里补扫）。

**但大概率已加载**：`System.loadLibrary` 在容器初始化早期就会用到（Hilt/Compose 等），而 `nativeInit` 在 `VirtualEngine.initialize()` 里。

### 3.3 风险清单

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| **ART 崩** | `android_dlopen_ext` 是 ART 加载所有 so 的必经之路，patch 后一旦有 bug 全崩 | 先在 `hooked_android_dlopen_ext` 里加"空实现直通"验证一次（只打日志不做事） |
| **命名空间相关** | `android_dlextinfo` 里有 `library_namespace`，`hooked_android_dlopen_ext` 是否完整转发 | 查现有实现是否透传全部字段 |
| **性能** | 每次 `System.loadLibrary` 都进 hook | 影响可忽略（加载次数很少） |
| **与 GPU 那次类似的风险** | patch 系统库可能引入不可预期行为 | 只 patch 这一个库，且可随时回退（加开关） |

---

## 四、社区方案参考

| 方案 | 做法 | 与本项目的关系 |
| --- | --- | --- |
| **Frida** | hook `android_dlopen_ext` 观察所有 so 加载 | 印证"这是唯一收敛点" |
| **bhook / shadowhook**（字节） | **inline hook**（改函数体，不依赖 GOT） | 如果 GOT patch 不可行，这是备选（需引入依赖） |
| **VirtualApp 系列** | 用 Java 层 hook `Runtime.loadLibrary` | **项目硬约束：VA 家族排除**，且 Java 层 hook 更脆弱 |

**我们的结论**：**优先用现有的 GOT patch 机制**（改动最小），**inline hook 作为备选**。

---

## 五、实施计划（四步，每步都可回退）

| 步 | 内容 | 验证 | 是否需要设备 |
| --- | --- | --- | --- |
| **1** | 加日志：`nativeInit` 时打印 `dl_iterate_phdr` 全部库名，确认 `libnativeloader.so` 在不在 | 看日志 | 是 |
| **2** | 把 `libnativeloader` 加入 patch 列表，`hooked_android_dlopen_ext` 只打日志不做事 | 装 guest 跑一遍，确认不崩 | 是 |
| **3** | 恢复 `hooked_android_dlopen_ext` 的正常行为（`patch_newly_loaded_libraries`） | 用 P28 复测：`fopen("r+")` 应**失败**（被重定向到沙箱，沙箱无文件） | 是 |
| **4** | 回归：Via 主页 / 长按 / 探针全量项 | 探针对比 + Via 手测 | 是 |

**步 3 的判据很干净**：
- 修复前：`P28 = fopen_r=ok fopen_rplus=ok fwrite=6`（写真实文件）
- 修复后：`P28 = fopen_r=fail(errno=2)`（沙箱里没这个文件 → 说明重定向生效）

---

## 六、这件事的真正价值

**不只是修"越权"**：

| 影响面 | 说明 |
| --- | --- |
| **路径隔离对第三方 native 库失效** | 任何通过 `System.loadLibrary` 加载的 so（探针、Via 的 native 部分、游戏引擎）**都能直接读真实路径** |
| **`P28` 暴露的只是冰山一角** | 之前所有"N 系列全失败"都没意识到是这个原因 |
| **maps 伪造也受影响** | `N10 maps pkg_hit=13` 能读到宿主痕迹，同样因为 hook 没覆盖 |

**所以这是"路径隔离是否真的成立"的根问题**，优先级应高于 P1-1/P1-3。
