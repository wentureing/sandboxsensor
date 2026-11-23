# 探针 v1.2 实测：直接安装 vs 本项目容器

- 日期：2026-11-22 19:20
- 探针：v1.2（`versionCode=3`，74 项）
- 容器：`p15`（`660b8f5`，hook 作用域重写版）
- 数据：`probe-reports/run-192038.txt` → 11 份 REPORT（4 份容器 / 7 份 installed）
- 对比表：`probe-reports/matrix-installed-vs-container.md`（58 项全部不同）

---

## 一、数据分组（先说清有几份）

| 报告 | mode 字段 | packageName | 实际环境 |
| --- | --- | --- | --- |
| #1–#7 | `installed` | `com.sandbox.probe` | **直接安装**（含历史重复运行） |
| #8, #11 | `container:dir-other(...)` | `com.sandbox.probe` | **容器内**（Context 已替换） |
| #9, #10 | `installed`（误判） | `com.nextvm.app` | **容器内**（Provider 早期，`getPackageName()` 还是宿主名） |

**两个附带发现**：
1. `detectMode()` 对 #9/#10 误判成 `installed` —— 因为它靠 `dataDir` 前缀判断，而这两个阶段 `dataDir` 还没换。
2. **容器在 `ContentProvider.onCreate` 阶段 `getPackageName()` 仍返回 `com.nextvm.app`** —— 这会污染所有基于 `pkg` 的检测（`J26`/`J27` 的字面量路径）。

---

## 二、最重要的发现：`P12` 证明 B1 阶段 A 可行 ✅

```
installed : libprobe.so missing in /data/app/.../com.sandbox.probe-.../lib/arm64
container : loaded=/data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_.../lib/libprobe.so
```

- 直接安装时 SO 内嵌在 APK（`extractNativeLibs=false`），`lib/arm64` 目录里没有文件
- **容器内用绝对路径 `System.load()` 成功了**

**结论**：容器的 `open`/`openat` hook **对 linker 有效**。B1（虚路径方案）阶段 A 的第一个前置条件已满足——linker 不会在 `open` 之前把虚路径判死。

---

## 三、B1 的核心缺口：字面量路径全部 ENOENT

| 探针 | 直接安装 | 容器内 |
| --- | --- | --- |
| `N03.open_literal` | `fd=139 path=/data/data/com.sandbox.probe/files/...` | **errno=2 (ENOENT)** |
| `N04.stat` | `size=0 mode=100600 uid=10309` | **errno=2** |
| `N05.access_files_dir` | `F_OK ok` | **errno=2** |
| `N06.fopen_write` | `wrote 5 bytes` | **errno=2** |
| `N07.openat_relative` | `dfd=140 fd=143` | **errno=2** |
| `N09.syscall_openat` | `fd=140` | **errno=2** |
| `N13.open_user0_prefix` | `fd=140 path=/data/user/0/com.sandbox.probe/...` | **errno=2** |
| `N15.stat_user0_dir` | `mode=40771 uid=10309` | **errno=2** |
| `J26/J27` 字面量往返 | `mkdirs=true read=literal` | **(empty)** |
| `J28` `exists(/data/data/<pkg>)` | `true` | **`false`** |
| `J29` `listFiles(/data/data/<pkg>)` | `6:cache,code_cache,files,...` | **`null`** |

**这就是 `01` 文档里那 48 项差异的来源**：容器只翻译"沙箱路径"，不认"虚拟路径"。任何用字面量 `/data/data/<pkg>` 的 SDK 都会失败。

---

## 四、检测面：这些地方会暴露

| 探针 | 直接安装 | 容器内 | 危险度 |
| --- | --- | --- | --- |
| **`N10.maps_scan`** | `lines=2990 pkg_hit=7 probe_lib=0` | `lines=3208 **pkg_hit=13 probe_lib=4**` | **P0** |
| **`P13` 宿主包可见** | `hidden: NameNotFoundException` | **`visible`** | **P0** |
| **`P16` 宿主 data 目录** | `exists=false canRead=false` | **`exists=true canRead=true`** | **P0** |
| `J18` deviceProtectedDataDir | `/data/user_de/0/com.sandbox.probe` | **`/data/user_de/0/com.nextvm.app`** | P0（直接泄漏宿主包名） |
| `J19` `getApplicationInfo().uid` | `uid=10309` | **`uid=0`** | P1（异常值） |
| `J20` `targetSdkVersion` | `35` | **`0`** | P1（异常值） |
| `P03` 同 UID 进程数 | `count=0`（修复前 bug） | **`count=2`**：`com.nextvm.app` + `com.sandbox.probe` | P2（阈值 >4 未触发） |
| `P04` 线程数 | 20 | 31 | — |
| `P22` mountinfo 行数 | 35 | **172** | P1（多 137 个挂载） |
| `P23` 沙箱挂载点 | 0 | **4** | P1（但内容疑似误报，见下） |
| `J25` SELinux | `s0:c53,c257,c512,c768` | `s0:c52,...` | P2（category 不同） |

### 一个自相矛盾的现象（重要）

- **`P05`（Java 读 `/proc/self/maps`）**：`lines=2972 **hostHits=0** sandboxHits=0`
- **`N10`（native 读 maps）**：`lines=3208 **pkg_hit=13** probe_lib=4`

**同一份数据，Java 看到干净的，native 看到 13 处泄漏。**

可能原因：
1. 容器的 `open`/`read` hook **对 Java 的 `FileInputStream` 生效**（返回过滤后的内容），但 native 侧用了别的方式读（`syscall(SYS_openat)` 或直接 `mmap`），绕过了 hook
2. 或者 Java 侧读到的 maps 被容器**主动过滤**了应用路径行

无论哪种，**结论都是：native 侧仍能读到真实 maps**，检测面没有真正关闭。这也说明「第 3 层 maps 伪造」必须做，而且必须覆盖 `syscall` 直调和 `mmap` 读。

> `N10` 的 `pkg_hit=13` 是可被外部检测直接利用的硬指标。

---

## 五、容器元数据的不一致（自身 bug，非检测面）

| 项 | 现象 |
| --- | --- |
| `J19` `getPackageManager().getApplicationInfo(pkg,0).uid` | **0**（应为 10308） |
| `J22` `ctx.getApplicationInfo().uid` | 10308 ✓ |
| `J20` `targetSdkVersion` | **0**（应为 35） |
| `J18` `deviceProtectedDataDir` | 指向宿主 `/data/user_de/0/com.nextvm.app` |

这三处都是容器在构造 `ApplicationInfo` 时漏填/填错，**与渲染崩溃无关，但会被加固 SDK 直接判死**。

---

## 六、结论与下一步

### 已验证可行

| 结论 | 依据 |
| --- | --- |
| **B1 阶段 A 可做** | `P12` 容器内绝对路径 `System.load` 成功 → open hook 对 linker 有效 |
| 现有路径翻译**只覆盖沙箱路径** | `N03/N04/N05/N06/N07/N09/N13/N15` 全部 ENOENT |
| 容器**已有部分 maps 过滤**（Java 侧） | `P05 hostHits=0` |
| 但**过滤不彻底**（native 侧） | `N10 pkg_hit=13` |

### 建议优先级

```
P0-1  修 ApplicationInfo 元数据：uid / targetSdkVersion / deviceProtectedDataDir
      （低成本、纯收益，直接消掉 3 个可检测特征）

P0-2  B1 阶段 A：虚路径 + 路径翻译
      -> 消掉 N03/N04/N05/N06/N07/N09/N13/N15 八项 ENOENT 与 J26-J29
      -> 前置条件已由 P12 验证

P0-3  maps 伪造（阶段 B）：必须覆盖 native syscall 直调
      -> 把 N10 的 pkg_hit 从 13 压到 0

P1    补 P13/P16：隐藏宿主包与宿主 data 目录
```

### 附：`P23` 的误报

`P23` 报 `sandboxMounts=4`，但样本内容形如 `- f2fs /dev/block/dm-57 rw,lazytime,...`，是**正常系统挂载**，不含沙箱路径。说明我的匹配条件（`contains("/virtual/")` 或 `contains("com.nextvm.app")`）命中了 `- f2fs /dev/...` 之后的字段，属于误报，需要收紧匹配（只看挂载点字段）。
