# 结论：哪些系统库**值得** hook（2026-11-28，基于 PLT 符号实测）

- 判据（用户 2026-11-28 明确）：**hook 后要么让环境更一致，要么解决崩溃/严重缺陷，要么有路径欺骗价值**；
  三者都不满足 ⇒ **不 hook**。
- 方法：解析每个库的 `.rela.plt`（`DT_JMPREL`），看**它的 PLT 里到底有哪些符号** ——
  PLT 里没有目标符号的库，patch 它等于**无效操作**（`patch_library_got` 找不到槽就直接返回）。

---

## 一、逐库实测（PLT 符号）

| 库 | PLT 里与"路径"相关的符号 | 总 PLT 符号数 |
| --- | --- | --- |
| **`liblog.so`** | `open`、`access`、`stat`、`chmod`、`mkdir`、`remove`、`rename`、`rmdir`、`opendir`、`closedir`、`readdir`、`fopen`、`__open_2` | 133 |
| **`libbase.so`** | `open`、`access`、`lstat`、`realpath`、`unlink`、`rmdir`、`mkstemp`、`nftw`、`__open_2` | 187 |
| **`libutils.so`** | 仅 `__open_2`（其余是 `dlsym`/`prctl`/线程原语） | 91 |
| **`libcutils.so`** | `open`、`openat`、`__open_2`、`__openat_2`、`lstat`、`mkdir`、`mkdirat`、`rename`、`unlink`、`fstatat` | 154 |
| **`libutilscallstack.so`** | 仅 `opendir`/`closedir`/`readdir`/`fopen`（**无 `open`**） | 54 |
| **`libdl.so`** | **无路径符号**（只有 `__loader_dlopen/dlsym/dl_iterate_phdr` 等） | 12 |
| **`libm.so`** | **无路径符号**（`isnanf`/`ldexp`/`memset`） | 5 |
| `libc.so` | **是 `open/stat/access` 的实现者**（不是调用方） | — |

---

## 二、逐个结论（按判据三问）

### 1. `liblog.so` —— **有符号，但不该 hook（负价值）**

| 判据 | 判断 |
| --- | --- |
| 环境一致？ | ❌ 它 open 的是**容器的日志文件**（`debug-trace.log`/`checks.log`），而容器诊断路径**本来就排除翻译** |
| 解决崩溃？ | ❌ |
| 路径欺骗价值？ | ❌ guest **不会**通过 `liblog` 查自己的路径 |

**额外风险**：`liblog` 是容器**自身**写诊断文件的通道。patch 它 ⇒ 每次日志 `open` 都走 `hooked_open`（多一次 `redirect_path`），
而这条路径**永远不该被翻译**（否则诊断会写进沙箱、或触发规则判定）。**纯开销 + 潜在破坏**。

### 2. `libbase.so` / `libcutils.so` —— **有符号，但同样不该 hook**

| 判据 | 判断 |
| --- | --- |
| 环境一致？ | 🟡 它们有 `realpath`/`lstat`/`fstatat`/`nftw`，理论上参与"路径形态查询" |
| 解决崩溃？ | ❌ |
| 路径欺骗价值？ | 🟡 **但收益方向错了**：这两个库是**容器自身**（`VirtualContext`/`SandboxManager`/诊断）大量使用的基础库；guest 侧的路径查询走的是 `libjavacore`（Java `File`）与 `libandroid_runtime` |

**结论**：patch 它们 ⇒ 容器的**自身路径判断**也被翻译（`realpath` 会把沙箱路径改成合成路径），
**导致容器内部逻辑与真实文件系统不一致** ⇒ 破坏隔离与诊断。**风险 ≫ 收益**。

### 3. `libutils.so` / `libutilscallstack.so` —— **无价值**

- `libutils`：只有一个 `__open_2`（fortify 变体），不参与路径查询；
- `libutilscallstack`：只 `opendir`/`readdir` 读 `/proc/self/maps` 做栈回溯（**调试用**，且它的 `opendir` 会命中我们的 `hooked_opendir`——**如果**该库被 patch，反而可能让栈回溯失效）。

**结论**：都不 hook（`libutilscallstack` patch 后**会让容器自己的栈回溯读不到 maps**，是负价值）。

### 4. `libc` 家族（`libc`/`libm`/`libdl`）—— **不适用**

| 库 | 结论 |
| --- | --- |
| `libc.so` | **它是 `open/stat/access/readlink` 的实现者**。patch 自己的 GOT 无意义（内部直接调，不经 PLT 到外部），且有自递归风险 |
| `libm.so` / `libdl.so` | PLT 里**没有**任何路径符号 ⇒ patch 无效 |

**"libc 家族有多大价值"的答案**：**零**。我们的 hook 机制只对**调用方库**有效；
`libc` 是被调方，`libm`/`libdl` 不碰文件路径。

---

## 三、真正值得 hook 的（按价值排序）

| 库 | 依据 | 价值 |
| --- | --- | --- |
| **`libjavacore.so`** | `libcTrace` 实测：24 条读取里 **22 条**由它发起（`who=libjavacore.so`）；PLT 里有 `open`（`javacore PLT I/O: hooked=[open]`） | **路径欺骗价值最高**：Java `File`/`FileInputStream` 的真实入口 |
| `libandroid_runtime.so` | `libcTrace` 里 1 条记录（`who=libandroid_runtime.so`） | 中：部分 native API（如 `AssetManager`、`SystemProperties` 的 native 侧） |
| `libandroidfw.so` | 未在 `libcTrace` 命中（via 场景未触发），但 `libhwui` 依赖它 | 待测：资源/Asset 读取 |

**但注意**：`libjavacore` 单库 patch 会导致 via 崩（`fontDirectory`，见 `调查-G3导致via崩溃.md`）⇒
价值最高 ≠ 现在可用；需要先解决"一致性集合"问题。

---

## 四、一句话总结

> **值得 hook 的是"guest 用来查路径的库"（`libjavacore` 等），不是"容器自己用的基础库"（`liblog`/`libbase`/`libcutils`）。**
> 后者即便 PLT 里有路径符号，patch 也只会让**容器自身逻辑与真实文件系统不一致**，是负价值。
> `libc` 家族不适用（实现者/无符号）。

---

## 五、对 `lib_deny` 的建议（不新增，只确认）

现有 6 类关键词已覆盖图形栈；**本节结论不需要新增 deny 条目** ——
因为 `liblog`/`libbase`/`libutils`/`libcutils`/`libutilscallstack` **本来就不在 `lib_allow` 里**
（`lib_allow` 默认空，且只会填 `libjavacore` 这类），它们走"系统库 ⇒ skip"的原路径。

> 即：**"不值得 hook"的库不需要 deny，只需要不把它们放进 allow。**
> deny 只用于"即使放进 allow 也不能 patch"的（图形栈那一类）。
