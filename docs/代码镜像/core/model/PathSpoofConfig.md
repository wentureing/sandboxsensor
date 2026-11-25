# PathSpoofConfig（core/model）

## 职责

路径欺骗配置的**唯一持久化入口**：豁免名单 + 每个路径族的开关。

**不负责**：
- 不判断"要不要合成"（那是 `PathPolicy`）
- 不产生路径字符串（那是 `VirtualConstants.synthetic*`，B 阶段收敛到 `PathKinds`）
- 不注册规则（那是 `NativeHookBridge`）

配置文件：`<containerDataDir>/files/path_spoof.conf`

## 函数

| 函数 | 作用 | 副作用 / 前提 |
| --- | --- | --- |
| `load(dir: File)` | 从 `dir/path_spoof.conf` 读入内存 | **清空并重建**内存态；文件不存在时生成模板（把 `mark.via` 放进豁免名单）。读失败时保持空集合 |
| `isLoaded(): Boolean` | 是否已执行过 `load` | 供 CmdReceiver 诊断 |
| `isGuestExempt(pkg)` | 该包是否豁免 | 纯内存查表 |
| `exemptList(): List<String>` | 豁免名单（已排序） | — |
| `setGuestExempt(pkg, exempt)` | 增/删豁免并写回文件 | 返回值=是否有变化；**写文件后需重启才生效** |
| `isKindEnabled(kindName)` | 该路径族是否启用（缺省 true） | — |
| `setKindEnabled(kindName, enabled)` | 开关某族并写回 | 同上 |
| `persist()` | 把内存态写回文件 | 保持人类可读格式（注释 + 分节） |

## 偏差登记

| 偏差 | 原因 | 类型 | 观测方式 |
| --- | --- | --- | --- |
| **读失败不报错、回落空集合** | 配置损坏不应让容器起不来；空集合 = 全部不豁免 = 旧行为 | 必要偏差 | `SPOOF_EXEMPT_LIST` 的 `loaded=` 字段 |
| **改配置不实时生效** | 用户明确要求"不要求实时生效"；实时生效需要在所有持有 `pathFidelity` 的地方重新求值 | 必要偏差 | CmdReceiver 输出带"重启后生效"提示 |
| `KNOWN_KINDS` 是硬编码字符串列表 | 与 `PathKind` 枚举重复；B 阶段改成遍历 `PathKind.entries` | **待修偏差** | 代码注释已标注"改这里要同步 PathKind" |
| 首次生成时硬编码 `mark.via` | 本轮验证目标；B 阶段应改为可配置的默认名单 | 中性偏差 | `writeTemplate` |

## 约束与坑

1. **必须在 `Application.onCreate` 调 `load(filesDir)`**，否则所有 `isGuestExempt` 都返回 false
   （= 豁免机制失效，但**不会崩**）。
2. **`load` 会清空内存态**：测试里靠这个在用例间重置；生产里只在启动时调一次。
3. **配置文件是行式 + `[节]` 语法**：解析靠 `substringBefore('#')` 去注释、
   `[..]` 识别节。**不支持转义、不支持嵌套**——够用即可，出问题能 `cat` 看懂。
4. **`object` 单例**：多进程（容器主进程 + guest 进程）各有独立内存态，
   但读的是同一个文件 → 改配置后两边都要重启才一致。
