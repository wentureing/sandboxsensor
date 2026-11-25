# PathPolicy（core/model）

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
