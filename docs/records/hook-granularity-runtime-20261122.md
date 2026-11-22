# Hook 粒度、检测面与运行时开关

- 日期：2026-11-22
- 关联：`hook-scope-analysis-20261122.md`、`root-cause-native-hook-pollution-20261122.md`

---

## 一、GOT patch 的粒度天花板

当前实现（`native-hook.cpp`）是 **GOT/PLT patch**，它的本质局限：

> **粒度是「库」，不是「调用方」。**

patch 了 `libfoo.so` 的 GOT 里 `open` 槽位 → **所有**通过 `libfoo.so` 调 `open` 的代码都被拦截，无法区分是"目标应用在调"还是"库内部自己在调"。

这就是为什么当初 patch GPU 驱动会出事：驱动自己调 `stat/fopen` 也被重定向了。

### 三种"按调用方"的方案

| 方案 | 原理 | 精度 | 成本 | 可行性 |
| --- | --- | --- | --- | --- |
| **A. 返回地址检查** | hook 函数里用 `__builtin_return_address(0)` 判断调用者模块，再决定是否重定向 | 中（能区分模块） | 每次调用一次地址→模块查找 | ✅ **推荐** |
| B. 每库独立 trampoline | 为每个库生成携带"库身份"的 hook 入口 | 高 | 代码生成复杂 | ⚠️ 复杂 |
| C. 内联 hook（inline） | 直接改写 `open` 函数入口指令 | 低（更不区分调用方） | 高（指令重定位、PAC、多线程） | ❌ 不适合 |
| D. seccomp + user-notify | 拦截 syscall | 最高（连内联 svc 都能拦） | 极高（每次 syscall 上下文切换） | ⚠️ 仅用于安全沙箱 |

#### 方案 A 的实现草图

```cpp
// 缓存：模块基址区间 -> 是否"目标应用"
struct ModRange { uintptr_t lo, hi; bool is_guest; };
static ModRange g_mods[64];
static std::atomic<int> g_mod_count{0};

static bool caller_is_guest(void* ret_addr) {
    const uintptr_t a = reinterpret_cast<uintptr_t>(ret_addr);
    const int n = g_mod_count.load(std::memory_order_acquire);
    for (int i = 0; i < n; i++) {
        if (a >= g_mods[i].lo && a < g_mods[i].hi) return g_mods[i].is_guest;
    }
    return false;   // 未知模块 -> 不重定向（保守）
}

ssize_t hooked_open(const char* path, int flags, ...) {
    if (!g_redirect_enabled.load(std::memory_order_relaxed)) {
        return real_open(path, flags, ...);
    }
    void* ra = __builtin_return_address(0);
    if (!caller_is_guest(ra)) {
        return real_open(path, flags, ...);   // 容器/系统库自己调 -> 直通
    }
    return redirect_open(path, flags, ...);   // 目标应用 -> 重定向
}
```

**收益**：可以安全地 patch 系统库（因为库内部调用会被识别为"非 guest"而直通），从而同时拿到：
- 高覆盖率（系统库代应用访问也能拦）
- 不自伤（容器/系统库自身调用直通）

**代价**：
- 每次调用一次线性扫描（模块数通常 < 30，缓存友好，可接受）
- `__builtin_return_address(0)` 在尾调用优化下可能失真（用 `-fno-omit-frame-pointer` 缓解）
- 需要维护模块加载/卸载表（挂在 `dlopen` hook 上，已有基础设施）

---

## 二、不拦截哪些调用会被发现 / 危害运行

### A. 会被"检测到在容器里"（对抗面）

| # | 调用 | 不拦截的后果 | 优先级 |
| --- | --- | --- | --- |
| 1 | `open("/proc/self/maps")` / `read` | 看到宿主 APK、`libnextvm-native.so`、沙箱路径 → **直接暴露** | **P0** |
| 2 | `readlink("/proc/self/exe")` | 返回宿主路径 | **P0** |
| 3 | `open("/proc/self/cmdline")` | 进程名是宿主包名（`com.nextvm.app:p0`） | **P0** |
| 4 | `open("/proc/self/status")` | `Uid:` 是宿主 UID | P1 |
| 5 | `getuid()` / `geteuid()` | 返回宿主 UID | P1 |
| 6 | `stat("/data/data/<guest_pkg>")` | ENOENT（guest 目录是虚拟的） | P1 |
| 7 | `access("/system/bin/su")` 等 root 探测 | 返回真实结果，与"已 root 环境"不符 | P2 |
| 8 | `__system_property_get("ro.*")` | 设备指纹可与其他数据交叉比对 | P2 |
| 9 | `fopen("/proc/self/task/*/comm")` | 线程名含宿主信息 | P2 |
| 10 | `dlsym` 后检查 GOT 是否被改 | 能发现 GOT patch | P2（难防） |

**结论**：**`/proc/self/maps` 是最高优先级**——一行 `open` + `read` 就能看到 `com.nextvm.app` 字样，几乎所有加固方案都会查。

### B. 会危害运行（功能/数据面）

| # | 调用 | 不拦截的后果 | 优先级 |
| --- | --- | --- | --- |
| 1 | `open(path, O_WRONLY/O_CREAT)` 字面量 `/data/data/<pkg>` | **写入失败或写进真实路径**（数据错乱/越权） | **P0** |
| 2 | `rename` / `unlink` / `mkdir` / `rmdir` | 同上，破坏性操作落真实路径 | **P0** |
| 3 | `mmap`（文件映射） | 映射到真实文件，内容/大小错乱 | **P0** |
| 4 | `opendir` / `readdir` | 看到真实目录内容（可能含其他应用数据） | P0 |
| 5 | `statfs` / `statvfs` | 容量数字错误（guest 应看到沙箱配额） | P1 |
| 6 | `stat` / `access` / `lstat` | 存在性判断错误 | P1 |
| 7 | `link` / `symlink` | 创建指向真实路径的链接（逃逸风险） | P1 |
| 8 | SQLite（经 `libsqlite.so`） | db 落在真实路径 → 数据混在一起 | **P0（需 NS_HOOK_SYSTEM_LIBS=1）** |
| 9 | `libmediandk` / `libwebviewchromium` 代访问 | 缓存/媒体文件落真实路径 | P1（同上） |

### C. 优先级结论

```
必须拦（P0）：写模式 open、rename/unlink/mkdir、mmap、opendir、/proc/self/maps、
              readlink /proc/self/exe、/proc/self/cmdline
有开关即可（P1）：getuid、stat/access、statfs、/proc/self/status
可延后（P2）：属性伪造、root 探测、线程名
```

---

## 三、运行时 hook（动态开关）

**结论：可以，而且应该做。但有三种做法，风险差别很大。**

| 做法 | 可行性 | 风险 | 说明 |
| --- | --- | --- | --- |
| **A. hook 函数内原子开关** | ✅ **推荐** | 极低 | 每次调用一次 relaxed 原子读（~1ns） |
| B. 运行时改 GOT（patch/unpatch） | ⚠️ 可行 | **高** | 多线程下其他线程可能正在用该槽位，需 `mprotect`+屏障 |
| C. 运行时 inline hook（frida 式） | ❌ 不建议 | 极高 | 指令重定位、PAC、并发 |

### 方案 A 的设计（推荐落地）

```cpp
// 全局开关（进程内有效；不同 guest 进程各自一份，天然按实例隔离）
static std::atomic<uint32_t> g_scope_mask{ SCOPE_ALL_DEFAULT };

enum ScopeBits : uint32_t {
    SCOPE_REDIRECT_IO   = 1u << 0,   // open/stat/fopen 等路径重定向
    SCOPE_HIDE_PROC     = 1u << 1,   // /proc/self/maps、cmdline、exe 隐藏
    SCOPE_SPOOF_ID      = 1u << 2,   // getuid、/proc/self/status
    SCOPE_SPOOF_PROP    = 1u << 3,   // __system_property_get 伪造
    SCOPE_SYSTEM_LIBS   = 1u << 4,   // 是否对系统库的调用方生效
};

#define SCOPE_ON(bit) (g_scope_mask.load(std::memory_order_relaxed) & (bit))

ssize_t hooked_open(const char* path, int flags, ...) {
    if (!SCOPE_ON(SCOPE_REDIRECT_IO)) return real_open(path, flags, ...);
    if (!caller_is_guest(__builtin_return_address(0))) return real_open(path, flags, ...);
    return redirect_open(path, flags, ...);
}
```

**优势**：
1. 开关是**进程级原子量** → 天然按 guest 进程隔离（`:p0` 关闭不影响 `:p1`）
2. 零重 patch 风险
3. 可以和 JNI 接口打通，让 Java 层（或 adb 调试通道）动态改：
   ```java
   nativeSetScope(mask);   // JNI
   ```
4. 便于做**灰度/按应用策略**：不同 guest 包名用不同 mask（存在 `VirtualApp` 配置里）

**唯一注意**：`.rela.plt` 的 patch 动作本身仍然只在库加载时发生一次——但**是否生效**由 mask 决定，所以"运行时开关"能力是完整的。

---

## 四、建议的落地顺序

```
第 1 步（当前 p14）：验证「容器自身 + 图形栈永不 patch」是否终结崩溃
第 2 步：加 caller_is_guest 返回地址检查
        -> 从而可以安全地把系统库重新纳入 patch 范围
        -> 解决 libsqlite / libmediandk 代访问问题（不需要 NS_HOOK_SYSTEM_LIBS 全局开启）
第 3 步：加 g_scope_mask 运行时开关 + JNI 接口
第 4 步：补 P0 检测面：/proc/self/maps、/proc/self/exe、/proc/self/cmdline
第 5 步：按 guest 包名差异化策略（存进 VirtualApp 配置）
```

---

## 五、一句话总结

> **GOT patch 只能按"库"划分，要么全拦（会自伤）要么不拦（会漏）；要按"调用方"划分，最实用的是在 hook 函数里查返回地址属于哪个模块。**
> 不拦截时最危险的不是"被检测"，而是**写路径类调用落回真实文件系统**（数据错乱）和 **`/proc/self/maps` 直接暴露宿主包名**。
> 运行时 hook 不需要改 GOT，只要在 hook 函数入口加一个原子 mask 判断，就能做到进程级、按能力的动态开关。

---

## 六、业界实证（联网调研）

### 6.1 已公开的容器检测手段（两个可直接复现的实现）

来源：[APK防护——Anti_Virtual App的思路和实现](https://blog.csdn.net/dfdhxb995397/article/details/101385162)、[ysrc/AntiVirtualApp](https://github.com/ysrc/AntiVirtualApp)

#### 手段 1：同 UID 进程计数（`ps | grep <uid_name>`）

原理：Android 靠 UID 隔离，正常应用一个 UID 只有一个进程；容器里 host + 多个 guest 共享 host 的 UID。

```c
struct passwd *pwd = getpwuid(getuid());
char cmd[20] = "ps | grep ";
strcat(cmd, pwd->pw_name);
// popen 后数行数
if (i > 4) { kill(0, SIGKILL); }   // 判定为 VA
```

**对我们的含义**：容器里 host + `:p0`、`:p1`… 必然多进程。这是**结构性特征**，靠 hook 文件路径补不了，只能在 `/proc` 枚举层面伪装（把非 guest 进程隐藏）。

#### 手段 2：`/proc/PID/maps` 的 SO 路径与 `/proc/PID/cmdline` 包名比对

```c
// 1) getpid()
// 2) 读 /proc/<pid>/cmdline 拿包名 p
// 3) 读 /proc/<pid>/maps，找 r-xp 段的 .so 路径
// 4) 正常环境 SO 只会出现在三处：
//      /data/app/<pkg>-xxx/lib/...
//      /data/data/<pkg>/...
//      /data/app-lib/...
//    若路径里 <pkg> 与 cmdline 的包名不一致 -> 判定为 VA
```

判定代码（原文）：

```c
if (strstr(path, p) != NULL) {
    if (startsWith(path, SO_APP_LIB))       { if (strncmp(path + SO_APP_LIB_LEN, p, len)) i++; }
    else if (startsWith(path, SO_DATA_APP)) { if (strncmp(path + SO_DATA_APP_LEN, p, len)) i++; }
    else if (startsWith(path, SO_DATA_DATA)){ if (strncmp(path + SO_DATA_DATA_LEN, p, len)) i++; }
}
```

**对我们的含义**：这是**最致命的一个**。我们的 guest SO 实际落在：

```
/data/app/com.nextvm.app-xxxxxx/lib/arm64/...           （宿主 APK 的 lib 目录）
/data/user/0/com.nextvm.app/files/virtual/apks/guest.apk!/lib/arm64/...
```

guest 读 `maps` 一眼就能看到 `com.nextvm.app`，与自己的包名不符 → **必被判定为容器**。

**结论升级**：`/proc/self/maps` 不只是 P0，它是**当前架构下最难绕过的一环**，因为它同时暴露：
- 宿主包名（`com.nextvm.app`）
- 沙箱路径（`files/virtual/...`）
- 宿主 SO（`libnextvm-native.so`）

### 6.2 PLT hook vs Inline hook（业界对比）

来源：[Android PLT hook 与 Inline hook](https://blog.csdn.net/linchaolong/article/details/147880722)

| 特性 | PLT / GOT hook（我们当前） | Inline hook |
| --- | --- | --- |
| Hook 层级 | 链接层（`.plt`/GOT） | 指令层（函数入口前几条指令） |
| Hook 对象 | 动态库导出的函数 | 任意函数（含非导出、库内静态函数） |
| 修改内容 | 函数地址指针 | 指令流（`b` / `ldr pc, [addr]`） |
| **粒度** | **库间调用（一次修改，该库所有调用者全命中）** | **可精确到任意函数、含库内调用** |
| 稳定性 | 较高 | 稍差（依赖指令结构、架构） |
| 跨平台 | 好 | 架构相关（arm32/arm64/thumb） |

**关键印证**：文中明说 PLT hook 的性质是"修改一次，全局生效（所有调用者都被 hook）"——这正是我们在 GPU 驱动上翻车的原因：**它天然无法区分调用方**。

业界成熟实现：**[ShadowHook](https://github.com/bytedance/android-inline-hook)**（字节跳动，用于 TikTok/抖音/今日头条/飞书，支持 thumb/arm32/arm64）。

### 6.3 对我们的直接结论

| 问题 | 结论 |
| --- | --- |
| 能不能"细致拦截" | 能。两条路：(a) 在 PLT hook 里查返回地址（改造量小）；(b) 换 inline hook（可拦库内调用，但稳定性/架构成本高） |
| 最该优先补的检测面 | **`/proc/self/maps`** —— 已被公开的检测方案直接针对，且暴露宿主包名 |
| 运行时开关 | 不需要换 hook 技术，原子 mask 即可；且 native hook 天然 per-process，多个 guest 进程互不干扰 |

### 6.4 一个必须承认的架构性事实

即使 hook 做得再细，`maps` 里的**实际映射**是内核产生的。要真正隐藏，只有三条路：

1. **拦 `open`/`read` 的 `/proc/*/maps`**，返回伪造内容（用户态可行，但内容要自己生成，且要处理 `pread`/`mmap` 变体）
2. **让 SO 真正落在与 guest 包名一致的路径**（即 `01` 文档里的 B1 方案：把路径做成 `/data/user/0/<guest_pkg>`）——这也是为什么 B1 的价值不只是"兼容 SDK"，更是**对抗检测**
3. 内核级隐藏（需要 root/模块，本项目明确不做）

→ **这反过来强化了 B1（标准路径）路线**：只要 guest 的 SO 真的在 `/data/user/0/<guest_pkg>/...`，`maps` 检测自然失效，不需要伪造。

