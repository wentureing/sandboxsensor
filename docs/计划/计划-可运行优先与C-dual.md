# 计划：可运行优先 + 欺骗可选（C-dual 路线，2026-11-27）

- 定位（用户 2026-11-27 明确）：
  1. **不正面对抗加固壳**，目标是**让它们能运行**；
  2. **更强的欺骗做成可选项**，不是默认前提；
  3. **欺骗所用的信息来自「容器对外采样」**（真实设备/环境），而不是硬编码常量。
- 第一验收案例：**`bin.mt.plus`（MT 管理器）的文件管理能在容器内运行**。
- 关联：`设计-项目设计理念.md`、`架构-hook实现现状-代码为准.md`、`架构-syntheticDataDir.md`、
  `现状-native-关键过程基线.md`、`计划-启动链已知缺陷（重构后修复）.md`（L3）

---

## 一、一句话

> 把"必须先骗过检测才能跑"倒过来：**先让 guest 真能读写（C-dual 真路径）**，
> 再把"骗"变成**分层可选项**，且骗的内容由容器**采样真实环境**生成，保证自洽。

---

## 二、三条原则

| # | 原则 | 含义 | 直接后果 |
| --- | --- | --- | --- |
| **P1** | **可运行优先** | 功能正确性不依赖"看起来像真机"；路径必须真实可读写 | 路径 I/O 走**真路径**，合成路径只用于"报告" |
| **P2** | **欺骗可选** | 默认最小欺骗；更强的欺骗按需开启 | 新增分级开关，默认值 = 现状或更低 |
| **P3** | **采样驱动** | 欺骗内容来自容器对外部环境的采样（Build / `/proc` / SELinux / mount / ABI / 密度 / 真实路径形状） | 新增 `EnvSampler`；换设备不需要改代码 |

---

## 三、C-dual 的落地形态

```
                        ┌─────────────── 报告路径（合成，看起来像真机）
guest API 调用 ─────────┤
                        └─────────────── 打开路径（真路径，沙箱里真实存在）
```

| 类别 | 例子 | 走哪条 | 现状 |
| --- | --- | --- | --- |
| **报告类** | `ApplicationInfo.dataDir`/`sourceDir`、`PackageInfo`、`getPackageInfo()` 的字段 | 合成 | 现状已是合成（`syntheticDataDir`/`pathFidelity`） |
| **打开类** | `getFilesDir()`/`getCacheDir()`/`getExternalFilesDir()`/`getDatabasePath()` 的**实际 I/O** | 真路径 | 已有开关 `VirtualContext.contentPathsUseReal`（默认 false）+ `PathWhitelistEffectTest` |

> **⚠ WebView 专用约束（2026-11-27 实测）**：给 **WebView 的 `file://`** 用的内容目录**不能**放在
> `getFilesDir()` 或 `getExternalFilesDir()` —— 两者都落在"app data 区"（内部 `/data/user/0/<pkg>/` 或外部 `Android/data/<pkg>/`），
> Chromium 会走内容嗅探 ⇒ `text/plain`。必须放**共享存储的非 app-data 目录**（`/storage/emulated/0/<自建目录>/`），
> 证据见 `记录-20261127-探针新增项实测.md` §3.2（W-ext-private=`text/plain`，W-ext-public=`text/html`）。
| **guest 自己拼路径** | `getOpPackageName()` 拼 `/storage/emulated/0/Android/data/<host>/…` | 兜底 hook（GOT / `javaIo`） | 现状靠 hook；C-dual 后降级为"可选兜底" |

**关键切换**：`contentPathsUseReal` 从"实验开关"逐步升级为**默认 true**（按 guest 分批），
而"报告类合成"保留 —— 这样 I/O 正确性不再依赖任何 hook。

---

## 四、采样层（新增组件，P3 的载体）

`core/common/.../EnvSampler`（纯采样，不做决策）：

| 采样项 | 用途（消费方） |
| --- | --- |
| `Build.*`（MODEL/DEVICE/MANUFACTURER/FINGERPRINT/…） | 可选的 Build 欺骗、探针期望值 |
| `/proc/version`、`/proc/cpuinfo` 摘要 | 内核相关检测面 |
| 真实 `Uid/Gid`、SELinux context | `status` 生成时"保留真实部分"（现在就是真 Uid） |
| `/proc/mounts` 里 `/data`、`/storage` 的选项 | 判断"是否看起来像真实挂载" |
| 已支持的 ABI、屏幕密度、语言 | 合成路径/ABI 相关欺骗 |
| **真实 APK/数据的路径形状**（`/data/app/~~xxx==/<pkg>-yyy==/base.apk`） | `synthetic_*` 与 maps 改写的模板来源 |

- **时机**：容器进程启动一次（`nativeInit` 之后、bind 之前），必要时可刷新；
- **落盘**：`files/env-snapshot.json`（debug 包可读，便于探针对照）；
- **不做**：不在这里做任何"伪装"，只采样 —— 伪装由消费方决定。

---

## 五、欺骗分级（P2 的载体）

| 级 | 内容 | 默认 | 说明 |
| --- | --- | --- | --- |
| **L0** | 无欺骗 | — | 仅路径可运行；最短路径、最易排错 |
| **L1** | **路径形状**欺骗 | 现状默认 | 合成 `ApplicationInfo` 字段、maps 行改写（`transform_maps_line`） |
| **L2** | **身份**欺骗 | 现状默认 | `/proc/self/{cmdline,comm,status}`、属性覆盖、uid 视角 |
| **L3** | **深度**欺骗（新增） | **关** | Build/SELinux/mount/root 痕迹/常见检测 SDK 的特征值 |

开关建议：`debug.nextvm.deception = 0|1|2|3`（分项覆盖仍走既有 `debug.nextvm.*`）。
**铁律**：L1–L3 只影响"报告内容"，**不允许影响 I/O 落点**（这是 P1 的保障）。

---

## 六、第一验收案例：MT 管理器

### 现状（实测）

- 设备上**没有 MT APK**（`/data/local/tmp` 只有 via/probe），容器里也没导入 → **需要你提供 APK**；
- 已知缺陷：`bin.mt.plus.canary` **启动即崩**
  `NoClassDefFoundError: l.۠᩹ܽ` → `VirtualApplicationManager.createApplication(VAM:1140)` ← `bindApplication(VAM:265)`（**legacy Step 2**），
  见 `计划-启动链已知缺陷（重构后修复）.md` §L3。

### 所以顺序是"先能启动，再谈文件管理"

| 步 | 目标 | 判据 |
| --- | --- | --- |
| **S1** | MT 能启动（修 L3：让崩溃带 `bind FAILED` + 原因，能修则修） | 到 `cb onActivityResumed`；或至少 breadcrumb 有 `bind FAILED <原因>` |
| **S2** | 文件管理可用 | 能列 `/sdcard`（沙箱映射）、进 `Android/data`、新建/改名/删除/复制；操作后文件**真实落在沙箱** |
| **S3** | 环境检测不阻断功能 | 若 MT 探测导致功能异常，开 L3 欺骗后功能恢复（**可选**，不是必须） |

### 为什么 MT 正好适合当第一个案例

- 它是**文件管理器**：所有动作都落在"路径是否真能读写"上 → 直接检验 C-dual 是否成立；
- 它有**加固/反调试成分**（类是混淆名）→ 正好检验"不强对抗也能跑"这条定位；
- 它的检测若只影响"提示/降级"，开启 L3 就有改善空间 → 验证"欺骗可选"的价值。

---

## 七、分阶段执行（每阶段独立可验收）

| 阶段 | 内容 | 判据 | 风险 |
| --- | --- | --- | --- |
| **0** | 采样层 + **暴露面量化**（不改行为） | `env-snapshot.json` 生成；探针 `J14–J18`/`J26–J29`/`P05/P06` 产出一张"若改真路径会暴露什么"的表 | 零 |
| **1** | `contentPathsUseReal` 打开试点（via/probe） | via 保存/WebView/SQLite 正常；探针无新 fail | 低（有开关可回退） |
| **2** | MT 启动（L3 缺陷） | 见 §六 S1 | 中（legacy bind 路径） |
| **3** | MT 文件管理 + 欺骗分级开关 | 见 §六 S2/S3 | 中 |

---

## 八、保留 / 降级 / 新增

| 现有设计 | 处置 | 理由 |
| --- | --- | --- |
| instance / slot / VAM 生命周期 / 进程模型 | ✅ 保留 | 与路径无关 |
| `VirtualContext`（含 `contentPathsUseReal`、`PathWhitelistEffectTest`） | ✅ **升级为主路径** | C-dual 的骨架已经在这里 |
| 豁免名单（via 零欺骗） | ✅ 保留并推广 | 它就是"用真路径"的特例 |
| Binder / AMS / PMS / Instrumentation | ✅ 保留 | 与路径无关 |
| **ProcSpoofing / maps 改写** | ✅ 保留 | 只改"报告内容"，正好服务 L1/L2 |
| 探针 / 自检 / breadcrumb golden | ✅ 保留（价值更高） | C-dual 与欺骗分级都只能靠它量化 |
| 路径规则表（`path_rules` / `path_spoof`） | ⚠️ 降级为**报告层美化** | 不再承担 I/O 正确性 |
| GOT patch（27 条 + 延迟 patch） | ⚠️ 降级为**可选兜底** | 只兜"guest 自己拼宿主路径"；`applied` 长期为 0 也可接受 |
| Java 层 `RegisterNatives`（`javaIo`） | ⚠️ 默认保持关，必要时作兜底 | C-dual 后更不需要 |
| `syntheticDataDir` / `pathFidelity` | ⚠️ 收敛到报告层 | 只给 `ApplicationInfo`/`PackageInfo` 用 |
| — | ➕ **新增** `EnvSampler` | P3 的载体 |
| — | ➕ **新增** `deception` 分级开关 | P2 的载体 |

---

## 九、明确不做

1. **不做 mount namespace / bind mount**（免 root 不可行）；
2. **不追求"过检测"**：L3 只在"能改善功能"时开启，不为对抗而对抗；
3. **不为单个 app 硬编码**：MT 的特殊需求要回到"采样 + 分级开关"，不许出现 `if (pkg == "bin.mt.plus")`；
4. **不把 L1–L3 的欺骗写进 I/O 路径**（P1 铁律）。

---

## 十、风险与对策

| 风险 | 对策 |
| --- | --- |
| 真路径后**包名暴露**（`/data/user/0/com.nextvm.app/...` 出现在 API/maps） | maps/status 伪装继续做（不依赖路径翻译）；L3 可按需再加；用探针量化暴露面 |
| MT 启动崩溃可能**比路径更根本** | 阶段 2 单独立项（L3），先让崩溃带原因 |
| `contentPathsUseReal` 全局打开导致个别 app 功能异常 | 按 guest 分批 + 开关（`debug.nextvm.contentReal`），保留 via 豁免路径 |
| 欺骗分级变成"又一套散落开关" | 统一 `debug.nextvm.deception`，并把每级的消费点写进本文档 |
| 没有 MT APK 无法验证 | **需要你提供**（设备上目前没有） |

---

## 十一、马上能做的第一步（不阻塞、零风险）

**阶段 0**：写 `EnvSampler`（只采样、不改任何行为）+ 在探针里把"报告路径"现状统计出来。
产出：`env-snapshot.json`（真实环境清单）+ 一张暴露面对照表 → 决定阶段 1 的默认值。

需要你确认两件事：
1. 是否按"**保留虚拟化底座 + 只换路径策略（C-dual）+ 加采样层/欺骗分级**"这个范围推进（不动 Binder/进程模型）；
2. MT 的 APK 能否提供（或指定一个同样"文件管理 + 加固"的替代 guest）。
