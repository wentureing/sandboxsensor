# 后续工作计划与难度评估（基于探针 v1.3 实测）

- 制定：2026-11-22
- 依据：`records/gaia-vs-ours-20261122.md`、`records/probe-baseline-vs-container-20261122.md`
- 前置修正：**`P14`、`P03` 不能作为绝对判据**（见 §0）

---

## 0. 先修正两个探针指标的语义

你指出的问题成立：这两个指标**强依赖运行环境**，绝对值没有可比性。

| 探针 | 为什么不能直接判 | 环境因素 |
| --- | --- | --- |
| `P14` `getInstalledPackages().size` | 同一台设备上装 164 个应用和装 3 个应用，这个数天然不同；容器只暴露 1 个或 3 个，未必说明隔离好 | 用户装了多少应用、系统应用数量、ROM 差异 |
| `P03` 同 UID 进程数 | 同一 UID 下系统可能还有别的进程；容器 host 是否常驻、guest 是否独立进程都影响它 | host 生命周期、guest 进程模型、系统进程回收 |

### 改成三种可用的判读方式

| 方式 | 做法 | 适用 |
| --- | --- | --- |
| **A. 同设备相对比较** | 容器内 / 直接安装 的比值。例如 `P14: 1 vs 164`、`P03: 1 vs 1` | 最可靠，**默认采用** |
| **B. 手动填写基线** | 报告里增加 `expected` 字段，由调用方通过 Intent extra / SharedPreferences 传入 | 需要固定基准时 |
| **C. 只作为线索** | 只在「容器内明显多于直接安装」时报警（如 `P03` 容器 5 vs 安装 1） | 快速筛查 |

### 探针侧改动（低成本，建议先做）

1. `ProbeRunner` 增加读取 `expected` 配置：
   ```java
   // ProbeActivity / ProbeProvider 从 Intent extra 或 prefs 读取
   // key: expected.P14, expected.P03
   ```
2. 报告条目增加 `expected` / `delta` 字段（有配置时）
3. `tools/probe-matrix.py` 增加 `--baseline installed` 参数，自动计算相对差异并高亮
4. 文档里把这两人项标为「**环境相关，需相对比较**」

**难度：低（0.5 天）**

---

## 1. 任务清单与难度评估

| # | 任务 | 难度 | 预估工作量 | 风险 | 主要改动面 |
| --- | --- | --- | --- | --- | --- |
| **T1** | 隐藏宿主包与宿主 data（`P13`/`P16`） | **低** | 0.5–1 天 | 中（可能误伤容器自身查询） | `core/framework` 的 PM 代理 + `core/hook` 的 stat |
| **T2** | 路径含 guest 包名 | **中** | 2–3 天 | **高**（全局路径拼接 + 旧沙箱迁移） | `core/sandbox`、`core/apk`、`core/virtualization` |
| **T3** | 修 `J19`/`J20`/`J18` 元数据 | **低** | 0.5 天 | 低 | `core/framework` 的 `ActivityThreadHook` |
| **T4** | maps 伪造覆盖 native 侧 | **高** | 3–5 天 | **高**（memfd + syscall/mmap 全覆盖） | 新建 `core/hook/procfs` 模块 |
| **T0** | 探针 `expected` 支持 + 相对比较 | **低** | 0.5 天 | 低 | `probe/` + `tools/probe-matrix.py` |

**合计：约 7–10 天**（不含测试回归）

---

## 2. 各任务详细计划

### T0：探针支持「环境基线」与相对比较（先做）

**为什么先做**：后续每个任务都要用探针验收，如果判据本身不可靠，验收会失真。

| 步骤 | 内容 |
| --- | --- |
| 1 | `ProbeRunner` 从 `Intent extra`（`expected.P03`、`expected.P14`）或 `SharedPreferences` 读期望值 |
| 2 | 条目输出增加 `expected`、`delta`（`delta = actual - expected`） |
| 3 | `probe-matrix.py` 加 `--baseline <列名>`，自动算比值并高亮「容器内显著偏离基线」的项 |
| 4 | 文档标注：`P03`/`P14` 为「环境相关」 |

**验收**：同一设备上跑两次（`expected` 填入直接安装的值），报告里能看到 `delta=0`。

---

### T1：隐藏宿主包与宿主 data（`P13`/`P16`）

**现状**（我们 vs Gaia）：

| 项 | 我们 | Gaia |
| --- | --- | --- |
| `getPackageInfo("com.nextvm.app")` | **visible** | `NameNotFoundException` |
| `stat("/data/data/com.nextvm.app")` | **exists=true canRead=true** | `ENOENT` |

**实现要点**：

1. **PM 代理**：在 guest 的 `PackageManager` 包装里，对「宿主包名」及容器自身的其他进程可见包抛 `NameNotFoundException`。
2. **native stat hook**：对 `/data/data/com.nextvm.app`、`/data/user/0/com.nextvm.app` 返回 `ENOENT`。
3. **必须区分调用方**（否则容器自己查自己会挂）：
   - 复用 `hook-granularity-runtime-20261122.md` §1 的**返回地址检查**思路
   - 容器自身调用 → 直通；guest 调用 → 拦截

**难点**：
- 容器内部代码（如 `AppManager` 扫描 guest 列表）也会调 `getInstalledPackages`，如果无差别过滤会破坏功能
- `stat` 的返回地址判断需要模块表（`dladdr` / maps 解析）

**验收**：探针 `P13` 变 `hidden`、`P16` 变 `exists=false`，且容器 UI 仍能正常列出 guest。

---

### T2：路径含 guest 包名

**目标形态**（学 Gaia，但可以更进一步）：

```
Gaia:     /data/user/0/com.app.hider.master.pro.cn/gaia/data/app/com.sandbox.probe/base.apk
我们现状: /data/user/0/com.nextvm.app/files/virtual/apks/com.sandbox.probe_1795346395414.apk
目标 A:   /data/user/0/com.nextvm.app/files/virtual/com.sandbox.probe/{apk,data,lib}
目标 B（更接近 B1）: 虚路径 /data/user/0/com.sandbox.probe/{lib,files} + 翻译层
```

**实现要点**：

1. 沙箱布局改为 `<guest_pkg>` 作为目录名（instanceId 作为子目录或后缀，处理多实例）
2. 全量迁移已有沙箱（`files/virtual/apks/<instance>.apk` → 新结构）
3. 所有拼路径的地方统一走一个 `SandboxPathResolver`
4. `ApplicationInfo.sourceDir` / `nativeLibraryDir` 同步指向新结构
5. 与 B1 阶段 A 对齐：虚路径 `/data/user/0/<guest_pkg>/...` 需要翻译层配合

**难点**：
- 路径拼接散落在 `core/apk`、`core/sandbox`、`core/virtualization`，需要全局梳理
- 多实例（同包名装两份）需要在目录名里保留区分
- 旧沙箱迁移不能丢数据

**验收**：`P09`/`P11`/`J16` 的路径里出现 `com.sandbox.probe`；`P12` 仍能 `System.load` 成功。

---

### T3：修 `J19`/`J20`/`J18` 元数据

| 项 | 现状 | 应为 |
| --- | --- | --- |
| `J19` `getApplicationInfo().uid` | **0** | 10308（宿主 uid，或 guest 应有的 uid） |
| `J20` `targetSdkVersion` | **0** | 35（来自 APK 解析） |
| `J18` `deviceProtectedDataDir` | `/data/user_de/0/com.nextvm.app`（宿主） | `<guest 沙箱>/data_de` |

**实现要点**：在 `ActivityThreadHook.kt` 构造/替换 `ApplicationInfo` 时补齐这三个字段。

**难度低**，但要注意 `uid` 语义：容器是共享 UID 模式，填宿主 uid 才与真实 `getuid()` 一致。

**验收**：三项与直接安装的语义对齐（`J19` 的 uid 等于 `P01` 的 uid）。

---

### T4：maps 伪造覆盖 native 侧

**现状**：

| 读法 | 结果 |
| --- | --- |
| Java `FileInputStream("/proc/self/maps")` | `hostHits=0`（容器已过滤） |
| native `N10.maps_scan` | **`pkg_hit=13 probe_lib=4`**（未过滤） |

**实现要点**：

1. 新建 `core/hook/procfs` 模块
2. Hook 层面：
   - `open`/`openat`（已有一个，扩展）
   - **`syscall(SYS_openat, ...)`**（N09 用的就是直调，会绕过普通 hook）
   - `mmap`（若检测方 mmap 读 maps）
   - `pread64`/`read`（对已打开的 fd）
3. 内容替换规则：
   ```
   /data/user/0/com.nextvm.app/files/virtual/...  →  <虚拟路径>
   含 com.nextvm.app 的整行                      →  删除
   ```
4. 用 `memfd_create` + 缓存（同一次 maps 内容短时间复用）
5. 处理变体：`smaps`、`smaps_rollup`、`numa_maps`、`task/*/maps`、`map_files/*`

**难点**（这也是它难度最高的原因）：

| 难点 | 说明 |
| --- | --- |
| syscall 直调 | 容器已 hook `syscall()`（N09 能被拦），但需确认覆盖所有 openat 变体 |
| fd 缓存一致性 | 若检测方 `open` 一次后多次 `read`，伪造内容必须稳定 |
| `stat` 大小 | memfd 的 `st_size` 与真实 maps 不同，需 hook `fstat` |
| 性能 | maps 每次读都要生成，需缓存 |
| 递归风险 | 生成伪造内容时自己也会读真实 maps → 必须用未 hook 的原始函数 |

**验收**：`N10` 的 `pkg_hit` 从 13 降到 0，且 `P05` 保持 0。

---

## 3. 推荐执行顺序

```
T0（探针基线能力）  ← 先做，否则后面的验收不可信
  ↓
T3（元数据 3 项）    ← 0.5 天，纯收益，立刻可用探针验证
  ↓
T1（P13/P16）        ← 0.5~1 天，消掉 2 个 P0
  ↓
T2（路径含包名）     ← 2~3 天，为 B1 铺路，也是 P09/P11 的改善
  ↓
T4（maps 伪造）      ← 3~5 天，最后做，因为它依赖前面路径形态稳定
```

**理由**：
- T3/T1 都是「小改动、立即可验证、消掉明确泄漏」
- T2 会改动全局路径，必须在前两者之后做（避免重复迁移）
- T4 依赖 T2 确定最终路径形态（伪造规则要按新形态写）

---

## 4. 验收矩阵（每个任务完成后跑一遍探针）

| 任务 | 关键探针 | 通过标准 |
| --- | --- | --- |
| T0 | `P03`/`P14` | 报告里有 `expected`/`delta`；矩阵工具能按基线高亮 |
| T3 | `J18`/`J19`/`J20` | `J19.uid == P01.uid`；`J20.targetSdk == 35`；`J18` 不含宿主包名 |
| T1 | `P13`/`P16` | `P13 == hidden`；`P16 == exists=false`；容器 UI 功能正常 |
| T2 | `P09`/`P11`/`J16`/`P12` | 路径含 guest 包名；`P12` 仍 loaded 成功 |
| T4 | `N10`/`P05` | `N10.pkg_hit == 0`；`P05.hostHits == 0` |

---

## 5. 风险清单

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| T1 无差别过滤破坏容器自身功能 | 容器 UI 无法列出 guest | 先做返回地址检查（`hook-granularity-runtime` §1）再过滤 |
| T2 全局路径改动引入回归 | guest 启动失败、数据丢失 | 保留旧布局兼容 + 迁移脚本 + 分阶段切换 |
| T4 伪造 maps 导致自身递归 | 崩溃 | 伪造生成走「未 hook 的原始函数」，加递归守卫 |
| 所有任务都需真机验证 | 无本地编译，CI 迭代慢 | 每任务独立构建 + 探针验收，避免批量改动 |

---

## 待办：导出通道（写豁免）

**状态**：未实现，等导入通道验证后再做。

**需求**：容器把 guest 生成的文件导出到公共目录（`/sdcard/Download/` 等）。

**为什么不能直接照抄导入的豁免**：

导入只用 `O_RDONLY`，而写会让 **guest 进程**也能往公共目录写文件，风险不同：

| 方向 | 调用方 | 风险 |
| --- | --- | --- |
| 导入（读） | 容器主进程 + guest | 低（公共目录本来可读） |
| **导出（写）** | **必须只允许容器主进程** | 高（guest 污染公共目录/覆盖用户文件） |

**实现要求**：

1. 在 `hooked_open` / `hooked_openat` 里对写模式（`O_WRONLY` / `O_RDWR` / `O_CREAT`）判断；
2. **必须过 `caller_is_guest()` 检查**（见 `hook-granularity-runtime-20261122.md` §1 的返回地址方案）：
   - 调用方是容器自身/AI 代理 -> 放行真实路径
   - 调用方是 guest -> 仍重定向进沙箱（或返回 EACCES）
3. 只豁免 `/sdcard/Download/`、`/storage/emulated/0/Download/`；
4. 需要配套：`hooked_rename`、`hooked_unlink`、`hooked_mkdir` 同样处理（否则能写不能删/改）。

**验收**：探针新增 `P28`（写测试）——容器主进程写 `/sdcard/Download/probe-export.txt` 成功、guest 写同一路径被拒或落沙箱。
