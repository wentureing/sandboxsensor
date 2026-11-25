# PathKind（core/model）

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
