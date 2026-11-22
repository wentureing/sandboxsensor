# 探针检测项清单（v1.2）

- 更新：2026-11-22
- APK：`probe-<sha>-arm64-v8a.apk`，`versionCode=3`，`versionName=1.2`
- 入口：`ProbeProvider.onCreate`（同步快照 + 后台 `ProbeRunner`）

---

## 设计原则

1. **每个大类对应一个"信息维度"**，而不是"一个 API 家族"。新增大类必须与既有维度不重叠。
2. 每条独立 `try`，单项失败不影响其余。
3. 只写 `dataDir`/`files` 下文件，不污染设备。
4. 不申请敏感权限，保证「直接安装」与「容器内」两次运行可比。

---

## 既有 10 个大类（v1.1）

| 大类 | ID | 覆盖 |
| --- | --- | --- |
| `context` | J01–J07 | `getDataDir`/`getFilesDir`/`getCacheDir`/`getCodeCacheDir`/`getNoBackupFilesDir`/`getDir`/SQLite |
| `external` | J08–J13 | 外部存储各目录 + `Environment.getExternalStorageDirectory` |
| `appinfo` | J14–J18 | `ApplicationInfo` 的 `dataDir`/`sourceDir`/`nativeLibraryDir`/`splitSourceDirs`/`deviceProtectedDataDir` |
| `pm` | J19–J21 | `getApplicationInfo`/`getPackageInfo`/`getInstalledPackages` |
| `identity` | J22–J25 | `myUid` vs `appInfo.uid`、pid/tid/process、`Build`、SELinux context |
| `literal` | J26–J29 | `/data/data/<pkg>`、`/data/user/0/<pkg>` 的字面量读写与列目录 |
| `canonical` | J30–J31 | `getCanonicalPath` 对已有/不存在路径的解析 |
| `javaio` | J32–J34 | `listFiles`、`SharedPreferences` 落盘、`dataDir` 列举 |
| `device` | J35–J36 | `SUPPORTED_ABIS`、`SUPPORTED_64_BIT_ABIS` |
| `native` | N01–N15 | libc 的 open/stat/access/fopen/openat/readlink/syscall/maps 扫描/`/data/user/0` 前缀 |

---

## v1.2 新增：7 个全新维度（P01–P23）

> 选择依据：`docs/records/hook-granularity-runtime-20261122.md` 的检测面清单，
> 以及 `docs/records/so-path-consistency-implementation-20261122.md` 的 B1 验证需求。

### ① `procstat` — /proc 进程与 UID 视角（P01–P04）

与 `identity` 的区别：`identity` 走 Java API（`Process.myUid()`），这一组**直接读内核文件**，能看到容器没代理到的地方。

| ID | 检测 |
| --- | --- |
| P01 | `/proc/self/status` 的 `Uid:/Gid:/Groups:/NSpid:` |
| P02 | `/proc/self/cmdline` 原始内容（进程名是否为 `com.nextvm.app:p0`） |
| P03 | **同 UID 进程计数** + 进程名样本 ← 对应 AntiVirtualApp 的 `ps \| grep <uid>` 手段 |
| P04 | `/proc/self/task` 线程数 |

### ② `maps` — 内存映射**内容**分析（P05–P08）

与 native `N10`（只验证能读 maps）的区别：这一组**分析内容暴露了什么**。

| ID | 检测 |
| --- | --- |
| P05 | maps 总行数 + **含 `com.nextvm.app` 的行数** + 含 `/virtual/` 的行数 |
| P06 | maps 里 `.so` 路径样本（前 4 条） |
| P07 | `readlink /proc/self/exe` 的结果 |
| P08 | `/proc/self/smaps` 可读性 |

### ③ `soload` — SO 加载链路（P09–P12）

直接验证 B1（虚路径方案）的前置条件。

| ID | 检测 |
| --- | --- |
| P09 | `ClassLoader.findLibrary("probe")` 返回的路径 |
| P10 | `System.mapLibraryName("probe")` |
| P11 | `nativeLibraryDir` 存在性 + 目录内文件数 |
| P12 | **用绝对路径 `System.load()` 自己的 so** ← B1 的可行性前置 |

### ④ `crosspkg` — 跨包可见性 / 逃逸面（P13–P16）

与 `literal`（只看自己的路径）完全不同的维度：**能看见多少不该看见的东西**。

| ID | 检测 |
| --- | --- |
| P13 | `getPackageInfo("com.nextvm.app")` 是否可见 |
| P14 | `getInstalledPackages` 是否含宿主条目 |
| P15 | `/data/data` 能否列目录、条目数 |
| P16 | `stat("/data/data/com.nextvm.app")` 的 exists/canRead |

### ⑤ `prop` — JVM 系统属性（P17–P19）

容器会改 Context 和文件，但**经常忘记改 JVM 属性**——一行 `println` 就能暴露宿主。

| ID | 检测 |
| --- | --- |
| P17 | `java.class.path`（是否含宿主 APK 路径） |
| P18 | `java.library.path` |
| P19 | `java.vm.name` + `user.dir` |

### ⑥ `fd` — 文件描述符视图（P20–P21）

路径翻译只在 `open` 时刻发生，**已经打开的 fd 会直接暴露物理路径**，是"翻译是否彻底"的照妖镜。

| ID | 检测 |
| --- | --- |
| P20 | `/proc/self/fd` 数量 |
| P21 | fd → canonical path 样本（前 5 条） |

### ⑦ `mount` — 挂载表（P22–P23）

容器若用 mount namespace 伪装路径会留下痕迹；反过来说，沙箱相关挂载点也会在这里暴露。

| ID | 检测 |
| --- | --- |
| P22 | `/proc/self/mountinfo` 可读性 + 行数 |
| P23 | 含 `com.nextvm.app` / `/virtual/` 的挂载点样本 |

---

## 判读对照（直接安装 vs 容器内）

| 类别 | 直接安装的期望 | 容器内的危险信号 |
| --- | --- | --- |
| `procstat` | `Uid` 与自身 uid 一致；cmdline = 包名；同 UID 进程数 = 1 | 同 UID 进程数 > 3；cmdline 含 `:p0` |
| `maps` | 无宿主包名；`.so` 都在 `/data/app/<自己>/lib` | **`hostHits > 0`（必现）** |
| `soload` | `findLibrary` 指向 `/data/app/<自己>/lib/arm64` | 指向宿主目录或沙箱 |
| `crosspkg` | 宿主包不可见；`/data/data` 不可列（Android 11+） | 宿主可见、`/data/data` 可列 |
| `prop` | `java.class.path` 不含宿主 APK | 含 `com.nextvm.app` |
| `fd` | fd 指向自己 data 目录 | fd 指向 `files/virtual/...` |
| `mount` | 无沙箱相关挂载 | 出现 `com.nextvm.app` 挂载 |

---

## 与 B1 验证的对应关系

| 探针 | 验证什么 |
| --- | --- |
| **P12** | 「绝对路径 `System.load` 能否成功」——若在容器里已成功，说明 open hook 对 linker 有效，B1 阶段 A 可做 |
| **P09/P11** | 虚路径方案落地后，这两个值应变成 `/data/user/0/<guest_pkg>/lib/arm64` 且 `exists=true` |
| **P05/P06** | 虚路径 + maps 伪造落地后，`hostHits` 应降到 0 |
| **P21** | 路径翻译是否覆盖到"已打开 fd"（当前只在 open 时刻翻译，这里会暴露差距） |

---

## v1.4 新增：第 8 个大类 `uiprobe`（P24–P27）

> 来源：`records/nextvm-defects-textselection-and-via-20261122.md` §9
> 目的：不用 UI 自动化，直接在容器内验证「长按触发文字选择」所依赖的窗口链路。

| ID | 检测 | 为什么重要 |
| --- | --- | --- |
| `P24` | `new View(activity)` 能否创建 | 容器内 Resources 是否绑定成功（失败会导致所有 UI 类探针不可用） |
| `P25` | `PopupWindow.showAtLocation` 是否成功 | **`FloatingToolbar`（文字选择菜单）就是 PopupWindow** |
| `P26` | `addView(TYPE_APPLICATION_ATTACHED_DIALOG)` | `FloatingActionMode` 用的正是这个窗口类型 |
| `P27` | `window/decorView` 的 token / attached | PopupWindow 的 anchor 是否可用 |

**实现要点**：
- 这些 API 必须在 **UI 线程 + Activity 存活**时调用 → 由 `UiProbe.run(activity)` 在
  `ProbeActivity.onCreate` 同步执行，结果暂存，再由 `ProbeRunner.probeUiEntries()` 合并。
- 每步独立 `try/catch`，失败记录异常类型（不会拖死整个探针）。
- 只用最裸的 `View(Context)`，避免 `Resources$NotFoundException`。

**判读**：

| 结果 | 含义 |
| --- | --- |
| `P25 showing=true` + `P26 added+removed ok` | 窗口链路正常 → 长按卡死的原因不在 popup |
| `P25 THREW ...` / `P26 THREW ...` | **确认断点在子窗口** → 容器的 WindowManager 代理不完整 |
| `P24 THREW Resources$NotFoundException` | 容器 Resources 未绑定，需要先修这个 |