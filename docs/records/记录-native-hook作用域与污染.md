# 记录-native hook 作用域与污染

- 更新：2026-11-23
- 合并自：`hook-scope-analysis`、`hook-granularity-runtime`、`root-cause-native-hook-pollution`
- 代码：`core/hook/src/main/cpp/native-hook.cpp`

---

## 一、事故：RenderThread SIGABRT（一次典型误诊）

### 1.1 症状

guest 启动后 `RenderThread` 崩溃，栈顶在 GPU 驱动里：
`GrGLCompileAndAttachShader → libllvm-glnext → abort`。

### 1.2 当时的错误归因

先怀疑"Adreno 驱动缺陷"，查了 Flutter / Chromium 的 workaround（`debug.hwui.renderer=skiagl`、
`LAYER_TYPE_SOFTWARE`），全部无效。

### 1.3 真实根因

**容器自己的 `dlopen` hook 污染了 GPU 驱动**：

```
容器 hook 了 dlopen
  → 顺带 patch 了 /vendor/lib64/egl/libGLESv2_adreno.so、eglSubDriverAndroid.so
    → 这两个库的 stat / fopen / __system_property_get 被重定向
      → 驱动编译 shader 时拿不到正确的缓存/属性
        → abort
```

**教训**：**栈顶在驱动里，不代表病因在驱动里。** 而 `GuestBreadcrumb`（199 处埋点）
能直接看到"guest 启动到哪一步"，比从结果倒推可靠。

---

## 二、作用域设计（4 层过滤）

`native-hook.cpp` 的过滤链，优先级从高到低：

```cpp
if (lib_name != nullptr) {
    // 1) 容器自身：永不 patch
    //    容器的路径重定向本身依赖真实的 open/stat/fopen，
    //    一旦被自己拦截，宿主 IO 会被错误改写
    if (strstr(lib_name, "com.nextvm.app") || strstr(lib_name, "libnextvm")) return;

    // 2) 图形栈：永不 patch
    //    实测 patch 后驱动编译 shader 时拿不到正确缓存/属性 → abort
    if (libGLES || libEGL || eglSubDriver || libvulkan || vulkan.adreno || libllvm-glnext) return;

    // 3) 应用/沙箱库：始终 patch
    if (/data/app/ || /data/user/ || /virtual/) { /* fallthrough */ }

    // 4) 其余系统库：由 NS_HOOK_SYSTEM_LIBS 决定（默认 0 = 不 patch）
    else {
#if NS_HOOK_SYSTEM_LIBS
        /* patch */
#else
        return;   // "GOT patch skipped (system lib, NS_HOOK_SYSTEM_LIBS=0)"
#endif
    }
}
```

### 2.1 为什么第 2 层要硬排除图形栈

| 库 | 后果 |
| --- | --- |
| `libGLESv2_adreno.so` | shader 编译 abort |
| `eglSubDriverAndroid.so` | EGL 初始化失败 |
| `libllvm-glnext.so` | **Adreno 的 GLES 编译器**（glnext = GL Next），同时被 Vulkan 和 GLES 共用 → **换后端也躲不掉** |
| `libvulkan.so` / `vulkan.adreno.so` | 同上 |

### 2.2 第 4 层的粗粒度问题

`NS_HOOK_SYSTEM_LIBS` 是**二元开关**——要么全 patch 系统库，要么全不 patch。
它只够区分"GPU 驱动"和"其他"，**不够区分"WebView"和"libsqlite"**。

**后果**：p13 之后 `libwebviewchromium*` 也被跳过，导致 WebView 读容器沙箱文件失败
（详见 `记录-WebView问题全链路.md`）。

**p17 尝试加精确白名单**（`libwebviewchromium*` 强制 patch），但**架构上无效**
（WebView 渲染在 isolated 进程），已回退到分支 `backup-webview-hook`。

**结论**：精确白名单的**方向**是对的，但**对 WebView 这个具体问题没用**。

---

## 三、粒度问题：hook 分不清"谁在调用"

### 3.1 症状

`redirect_path()` 是**进程级**的（hook 装在哪个进程，就影响哪个进程的所有调用）。
它拿不到调用方信息，所以：

| 场景 | 问题 |
| --- | --- |
| 容器自己 `System.load("/sdcard/x.so")` | 被错误重定向进沙箱 |
| guest `dlopen("/data/app/.../libfoo.so")` | 需要翻译到沙箱 |
| 容器自己读 `/sdcard/Download/x.apk`（导入） | 被重定向 → 读不到真实文件 |

### 3.2 可行的解法：返回地址判别

原理：**调用方属于哪个映射区间，可以从返回地址判断**。

```cpp
static bool caller_is_guest(void* return_address) {
    Dl_info info;
    if (dladdr(return_address, &info) == 0) return false;
    const char* p = info.dli_fname;
    // 容器自己的库 → false
    if (strstr(p, "com.nextvm.app") || strstr(p, "libnextvm")) return false;
    // 应用/沙箱库 → true
    return strstr(p, "/data/app/") || strstr(p, "/data/user/") || strstr(p, "/virtual/");
}
```

**局限**（必须诚实记录）：

| 局限 | 说明 |
| --- | --- |
| 需要拿到返回地址 | 部分 hook 点（如 `__system_property_get`）不容易取 |
| 内联/尾调用优化 | `-O2` 下返回地址可能不可靠 |
| 间接调用 | 通过函数指针转发时，`dladdr` 可能落到中转函数 |

**所以只对"关键路径"用**（如 `open` 的导入豁免、写通道），不对全部 hook 用。

---

## 四、现有的路径豁免（p18）

```cpp
static bool is_import_read_exempt(const char* path) {
    return strncmp(path, "/sdcard/Download/", 18) == 0 ||
           strncmp(path, "/storage/emulated/0/Download/", 30) == 0 ||
           strncmp(path, "/data/local/tmp/", 17) == 0;
}

// hooked_open / hooked_openat 里：
if ((flags & O_ACCMODE) == O_RDONLY && is_import_read_exempt(path)) {
    return real_open(path, flags);     // 不重定向
}
```

**只豁免只读**。写通道留待后续，且**必须过 `caller_is_guest` 检查**（防止 guest 污染公共目录）。

**为什么导入需要这条**：
- `CmdReceiver` 的 `INSTALL --es path` 走路径通道（`File` → `FileInputStream` → `open()`）→ 被重定向 → 读不到真实 APK
- 而容器 UI 的"browse APK"走 SAF → `ContentResolver.openInputStream` → **fd**（fd 不经过路径解析）→ 能导入

**这就是"fd 绕过路径限制"在本项目的第一次显形**，后来在 WebView 上又出现一次。

---

## 五、WebView 那次事故的特殊性

p13 把系统库整体排除，是为了修 GPU 崩溃 —— **这个决定本身是对的**。

但它**顺带**让 `libwebviewchromium*` 也被跳过，而当时**没有人测过 guest 内的 WebView**
（探针没有 WebView 项）。

**教训**：
1. 粗粒度的"全有/全无"开关，其**副作用不会立刻显现**
2. 需要**探针覆盖**才能发现（这也是 `计划-提升路线图.md` 里 P1-1 自检的动机）

---

## 六、当前作用域的完整清单

| 类别 | 举例 | 是否 patch | 理由 |
| --- | --- | --- | --- |
| 容器自身 | `libnextvm*`、`com.nextvm.app/**` | ❌ | 容器 IO 依赖真实路径 |
| 图形栈 | `libGLES*`、`libEGL*`、`eglSubDriver*`、`libvulkan*`、`vulkan.adreno*`、`libllvm-glnext*` | ❌ | shader 编译 abort |
| 应用/沙箱 | `/data/app/**`、`/data/user/**`、`/virtual/**` | ✅ | 需要路径翻译 |
| 导入豁免（只读） | `/sdcard/Download/`、`/data/local/tmp/` | ❌（不重定向） | 让 `INSTALL` 能读真实 APK |
| 其余系统库 | `libsqlite`、`libwebviewchromium*` 等 | ❌（`NS_HOOK_SYSTEM_LIBS=0`） | 风险未评估 |
