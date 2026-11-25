# PathSpoofConfig（core/model）

## 职责

路径欺骗配置的**唯一持久化入口**：豁免名单 + 欺骗名单 + 每个路径族的开关。

**不负责**：
- 不判断"要不要合成"（那是 `PathPolicy`）
- 不产生路径字符串（模板在 `PathKinds`）
- 不注册规则（那是 `NativeHookBridge`）
- 不写日志、不做 I/O 之外的副作用（只有 `load`/`persist` 碰文件）

配置文件：`<containerDataDir>/files/path_spoof.conf`

```ini
# 路径欺骗配置 —— 改完需重启容器/guest 生效

[豁免名单]
# 这些包不做任何路径形态欺骗（不产生合成路径），但仍隔离到沙箱
mark.via

[欺骗名单]
# 允许产生合成路径的包（反双开）；不在本段也不在豁免段 = 直接用沙箱路径
# 每行一个包名；与豁免名单冲突时豁免优先
com.greenpoint.android.mc10086.activity

[各族开关]
FILES=on
OBB=on
```

## 函数

| 函数 | 作用 | 副作用 / 前提 |
| --- | --- | --- |
| `load(dir: File)` | 从 `dir/path_spoof.conf` 读入内存 | **清空并重建**内存态；文件不存在时生成模板（`DEFAULT_EXEMPT_PKG` 进豁免、`CM_PACKAGE` 进欺骗）。读失败时保持空集合 |
| `isLoaded(): Boolean` | 是否已执行过 `load` | 供 CmdReceiver 诊断；也是 `isPathFidelityGuest` 的回落判据 |
| `isGuestExempt(pkg)` / `exemptList()` | 豁免查询 | 纯内存查表 |
| `setGuestExempt(pkg, exempt)` | 增/删豁免并写回文件 | 返回值=是否有变化；**写文件后需重启才生效** |
| `isSpoofListed(pkg)` / `spoofList()` | 欺骗名单查询 | 纯内存查表 |
| `setSpoofListed(pkg, listed)` | 增/删欺骗名单并写回 | 同上 |
| `isKindEnabled(kindName)` | 该路径族是否启用（缺省 true） | — |
| `setKindEnabled(kindName, enabled)` | 开关某族并写回 | 同上 |
| `persist()` | 把内存态写回文件 | 保持人类可读格式（注释 + 分节） |
| `DEFAULT_EXEMPT_PKG` | 模板默认豁免包（`mark.via`） | **只是默认值**，运行时改文件即可 |

## 偏差登记

| 偏差 | 原因 | 类型 | 观测方式 |
| --- | --- | --- | --- |
| **读失败不报错、回落空集合** | 配置损坏不应让容器起不来；空集合 = 全部不豁免 = 旧行为 | 必要偏差 | `SPOOF_EXEMPT_LIST` 的 `loaded=` 字段 |
| **改配置不实时生效** | 用户明确要求"不要求实时生效"；实时生效需要在所有持有 `pathFidelity` 的地方重新求值 | 必要偏差 | CmdReceiver 输出带"重启后生效"提示 |
| **豁免与欺骗两份名单分开存** | 语义相反（"完全不欺骗" vs "允许欺骗"），合并会让"同时列出"变得含意不明；冲突时豁免优先由 `isPathFidelityGuest` 保证 | 必要偏差 | H13（两份名单互不影响） |
| **默认模板里写入了具体包名** | 首次启动需要种子，否则 via 会走欺骗、重演 WebView 读不到本地文件 | 中性偏差 | 模板内容即配置；改默认只改 `DEFAULT_EXEMPT_PKG` / `CM_PACKAGE` |
| ~~`KNOWN_KINDS` 硬编码字符串列表~~ | **已修（2026-11-25）**：改为 `PathKind.entries.map { it.name }` | 已消除 | — |
| ~~首次生成硬编码 `mark.via`~~ | **已改为可配置**：`DEFAULT_EXEMPT_PKG` + 配置文件 + 运行时可增删 | 已消除 | W1–W5 |

## 约束与坑

1. **必须在 `Application.onCreate` 调 `load(filesDir)`**，否则 `isLoaded()=false` →
   `isPathFidelityGuest` 回落到内置默认（只有 CM），via 不会被豁免。
2. **`load` 会清空内存态**：测试里靠这个在用例间重置；生产里只在启动时调一次。
3. **行式 + `[节]` 语法**：`substringBefore('#')` 去注释、`[..]` 识别节。
   **不支持转义、不支持嵌套**——够用即可，出问题能 `cat` 看懂。
4. **`object` 单例**：容器主进程与各 guest 进程各有独立内存态，但读同一个文件 →
   改配置后两边都要重启才一致。
5. **日志约束**：本类不写日志；消费方只能走 `DebugTrace`（`DebugFlags.isDebugBuild()` 为假时
   首行 return，release 不进产物）。
6. **不要在这里加"某包的专门逻辑"**：所有差异都通过两份名单表达，代码里不写死包名。

## 可测性

自动化验证不依赖 Android 运行时（`PathWhitelistEffectTest`，`core:sandbox`）：

```
path_spoof.conf 两份名单
  → VirtualConstants.isPathFidelityGuest      （W1/W2/W3/W5）
  → shouldUseSyntheticContent(pathFidelity, contentPathsUseReal)   （W6，"看到什么路径"的唯一判据）
  → PathPolicy.isRedirectionEnabled + PathKinds.rulesFor           （W7，"写入仍落沙箱"的结构前提）
```
