# PathKinds（路径族数据表）

- 代码：`core/model/src/main/java/com/nextvm/core/model/PathKinds.kt`
- 测试：`core/hook/src/test/java/com/nextvm/core/hook/path/PathKindsTest.kt`（K1–K7）
- 计划：`docs/输出/变更/20261125-目录配置集中管理.md`
- 日期：2026-11-25

---

## 职责

**负责**：14 个路径族的三种形态（真机形态 / 沙箱相对位置 / 需要翻译的宿主前缀）的**唯一维护处**，
以及从这些数据推导出「规则对 `(from, to)`」的纯函数。

**不负责**（边界）：

| 不负责 | 在哪 |
| --- | --- |
| 是否启用合成路径 | `PathPolicy.isSpoofEnabled(kind, pkg)` |
| 何时注册规则、是否 `ensureDir` | `NativeHookBridge` / `VirtualContext` / `PackageManagerProxy`（控制流） |
| 路径匹配语义（前缀 / 族根 / 精确） | `NativeHookBridge.translatePath` + C++ `redirect_path`，见 `docs/输出/记录/记录-T39-T41-族根路径无规则.md` |
| 实际的沙箱目录创建 | `SandboxManager`（`ensureDir`） |
| 任何日志与 I/O | **本文件不写日志、不建目录、无状态** |

**一句话**：表只回答「这个族的三种形态长什么样」，不回答「谁在什么时候用它」。

## 函数

| 函数 | 签名 | 作用 / 关键前提 |
| --- | --- | --- |
| `ALL` | `List<PathKindSpec>` | 14 族数据；顺序即持久化顺序（`PathSpoofConfig` 由此派生族清单） |
| `of` | `(kind) -> PathKindSpec` | 取族；`kind` 必须存在（否则抛） |
| `synthetic` | `(kind, pkg, type = null) -> String` | 真机形态；`type` 仅 `DATABASES`/`APP_DIR`/`EXTERNAL_FILES` 有意义 |
| `sandboxRelOf` | `(kind, pkg, type = null) -> String?` | 相对 `<instanceDir>`；`null` = 由调用方给绝对路径（仅 APK） |
| `hostForms` | `(kind, pkg) -> List<String>` | 需要翻译的宿主前缀（含/不含尾斜杠两种）；空 = 由 `coveredBy` 覆盖 |
| `rulesFor` | `(owner, pkg, sandboxDataDir, apkPath = null) -> List<Pair<String,String>>` | **纯函数**：产出规则对，不注册；`apkPath` 为 null 时跳过 APK 族 |
| `hostFallbackForms` | `(hostPkg) -> List<String>` | 宿主包名兜底的 from（guest 用 `getOpPackageName()` 拼路径时命中） |
| `hostFallbackRules` | `(hostPkg, guestPkg, sandboxDataDir) -> List<Pair<String,String>>` | 兜底规则对；目标**复用** `EXTERNAL_DATA` 的沙箱位置，防止两处分叉 |
| `EXTERNAL_ROOT_FORMS` / `EXTERNAL_ROOT_BARE_FORMS` | `List<String>` | 共享存储根（`/sdcard/` 等，**不含包名**，不属于任何族） |
| `kindsWithoutHostForms` | `() -> List<PathKind>` | 结构自检：既无宿主前缀又无 `coveredBy` 的族（正常应为空） |

**规则数契约**（K4 守着）：

```
APP            = 2 (DATA_ROOT) + 4 (EXTERNAL_DATA) + 4 (OBB) + 4 (MEDIA) = 14
PATH_FIDELITY  = 1 (APK) + 1 (NATIVE_LIB)                                =  2
hostFallback   = 2（宿主包名，可选）                                      =  2
合计                                                                     = 18
```

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | 合成路径**不含 instanceId** | 真机形态 `/data/user/0/<pkg>` 本身不含实例；加槽位会让形态失真 | **必要偏差**（当前） | 同包装两份 → 规则 key 冲突后注册覆盖 → 数据互串。**多实例开发暂停**，恢复前先改本表签名 |
| 2 | `DATA_ROOT` 同时注册 `/data/data/<pkg>/` 与 `/data/user/0/<pkg>/` | 真机上两者是同一目录的两种写法（后者是前者的指向目标） | 必要偏差 | 规则对条数（K4） |
| 3 | APK 的沙箱位置**不在实例目录下**（`virtualRoot/apks/<instance>.apk`） | 上游布局如此；APK 是共享导入产物，不随实例数据删除 | 中性偏差 | `sandboxRelOf(APK) == null`（K3） |
| 4 | `EXTERNAL_ROOT_BARE_FORMS`（`/sdcard`、`/storage/emulated/0`）与 `EXTERNAL_ROOT_FORMS` 的根形态重复 | 历史：为避免"无斜杠不命中"手工补的变体 | **待修偏差**（匹配语义统一后合并） | 见 `记录-T39-T41-族根路径无规则.md` 方案 A |
| 5 | `hostFallback` 与 `EXTERNAL_DATA` 目标相同、来源不同 | guest 用 `getOpPackageName()` 会拼出宿主包名 | 必要偏差（上游机制） | `hostFallbackRules` 的目标由 `EXTERNAL_DATA` 派生，结构上不会分叉 |

**与 AOSP 的偏差**：`syntheticDataDir` 等只是字符串形态，**不改变任何系统调用语义**；
AOSP 的 `getDataDir()` 返回值无尾斜杠，本表保持该形态（K2 钉住）。

## 约束与坑

| # | 约束 | 违反后果 |
| --- | --- | --- |
| 1 | **单向依赖**：`VirtualConstants.synthetic*` 委托本表；本表只引用 `const val SYNTHETIC_INSTALL_TOKEN` | 若反过来让本表调用 `VirtualConstants.synthetic*` 会形成初始化环 |
| 2 | 新增族必须同时给 `hostForms` 或 `coveredBy` | `kindsWithoutHostForms()` 非空 → K6 红 → 线上表现为「拿到假路径但读写失败」 |
| 3 | 规则条数是数据契约（18） | K4 用**手写期望集合**比较，改数据必须同时改测试并解释 |
| 4 | `hostForms` 必须含「尾斜杠 + 无尾斜杠」两种形态 | `getObbDir()` 返回无尾斜杠、子路径带斜杠；只写一种会重演 `2fcfbc7` 的"写到一个地方读另一个" |
| 5 | **不写日志**；消费方接线时只允许用 `DebugTrace`（`DebugFlags.isDebugBuild()` 为假时首行 return，release 不进产物） | 在数据层打日志会破坏"日志只在 debug 产物"的约定 |
| 6 | 消费方**不得让日志写入经过被 hook 的层**（`debug-trace.log` 的写入必须不经路径翻译/不触发 hook 日志） | 日志会被重定向或递归刷屏；已有两道保护：`is_container_diag_path()` 跳过自身诊断文件（`438eab2`）+ 容器自身库永不 patch |
| 7 | 多实例**暂停**：不要给 `synthetic`/`sandboxRel` 加 slot 参数 | 会牵动 24 条规则与 `VirtualContext`/`PackageManagerProxy` 全部调用点；恢复前先改 `docs/架构/架构-syntheticDataDir.md` |

## 当前状态（2026-11-25）

- **数据层已落地**，消费方**尚未接线**：`VirtualContext` / `PackageManagerProxy` / `NativeHookBridge`
  仍用各自的写法（`VirtualConstants.synthetic*` 已委托本表，所以"合成路径"这一半已经统一）。
- 下一步（未做）：`setupAppRedirections` 改为遍历 `rulesFor(APP)`；
  `VirtualContext` 的 13 处判断改为查 `PathPolicy`。验收判据是 `getPathRedirectSnapshot()` diff 为空。
