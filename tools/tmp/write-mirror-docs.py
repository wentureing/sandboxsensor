import io
import os

BASE = "docs/代码镜像/core/model/"
os.makedirs(BASE, exist_ok=True)

DOCS = {
"PathSpoofConfig.md": """# PathSpoofConfig（core/model）

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
""",

"PathKind.md": """# PathKind（core/model）

## 职责

路径族的**分类枚举**：每个值对应一种"真机上的目录形态"。

**不负责**：
- 不承载模板（合成路径字符串在 `VirtualConstants.synthetic*`，B 阶段收敛到 `PathKinds`）
- 不做策略判断（那是 `PathPolicy`）

## 函数

| 成员 | 说明 |
| --- | --- |
| 14 个枚举值 | `DATA_ROOT` / `FILES` / `CACHE` / `CODE_CACHE` / `NO_BACKUP` / `DATABASES` / `APP_DIR` / `EXTERNAL_DATA` / `EXTERNAL_FILES` / `EXTERNAL_CACHE` / `OBB` / `MEDIA` / `APK` / `NATIVE_LIB` |
| `fromName(name)` | 按名字查枚举，未知返回 null（供配置解析用） |

## 偏差登记

无。这是纯分类，不涉及行为。

## 约束与坑

1. **新增路径族时必须同步三处**：
   - 本枚举
   - `PathSpoofConfig.KNOWN_KINDS`（字符串列表）
   - `VirtualConstants.synthetic*` 与 `NativeHookBridge` 的规则
   B 阶段会把后两处收敛成数据表，届时只剩两处。
2. 枚举名字符串会写进配置文件（`FILES=on`），**改名等于改配置格式**，要谨慎。
""",

"PathPolicy.md": """# PathPolicy（core/model）

## 职责

"要不要产生合成路径"的**唯一判断入口**，保证豁免优先级全局唯一。

**不负责**：
- 不读配置文件（那是 `PathSpoofConfig`）
- 不产生路径字符串
- **不控制沙箱隔离**（`isRedirectionEnabled` 恒 true，见下）

## 函数

| 函数 | 作用 | 优先级 |
| --- | --- | --- |
| `isGuestExempt(pkg)` | 是否豁免（转发 `PathSpoofConfig`） | — |
| `isSpoofEnabled(kind, pkg)` | 该族是否**产生合成路径** | 1. 豁免 → false<br>2. 该族开关 off → false<br>3. 在 `PATH_FIDELITY_WHITELIST` → true<br>4. 其余 → false |
| `isRedirectionEnabled(pkg)` | 是否注册翻译规则（隔离） | **恒 true** |

## 偏差登记

| 偏差 | 原因 | 类型 | 观测方式 |
| --- | --- | --- | --- |
| `isRedirectionEnabled` 恒返回 true，没有开关 | 「豁免」的定义就是"只关欺骗、不关隔离"。若也关掉规则，guest 会读写真宿主 `/sdcard`——那是取消容器，不是豁免 | 必要偏差 | 单测 H9 钉住 |
| 返回值只表达"是否合成"，不表达"用什么路径" | 控制流与数据分离：取路径是 `SyntheticPathProvider`（B 阶段）的事 | 必要偏差 | 代码结构本身 |

## 约束与坑

1. **所有"要不要合成"的判断都必须走这里**。绕过它直接查
   `VirtualConstants.isPathFidelityGuest` 会漏掉豁免和族开关。
2. **`isSpoofEnabled == false` 不等于"路径不翻译"**：规则仍注册，
   guest 拼出的宿主路径照样被翻译到沙箱。这两件事在语义上必须分开。
3. **`VirtualContext.pathFidelity` 是构造时求值的 `private val`**：
   豁免状态一变，**已创建的 Context 不会跟着变** → 这就是"改配置需重启"的根本原因。
""",
}

for name, content in DOCS.items():
    p = os.path.join(BASE, name)
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  {p}  ({len(content.splitlines())} 行)")
