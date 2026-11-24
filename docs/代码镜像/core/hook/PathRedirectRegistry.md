# 代码镜像：`core/hook/PathRedirectRegistry.kt`

> v154 从 `NativeHookBridge.kt` 拆出（原生 1063 → 995 行，达标 <1000）。

## 职责

**做什么**
- 维护「前缀 → 目标前缀」映射：`add` / `remove` / `clear` / `snapshot` / `size`
- 路径翻译 [translate]：**最长前缀匹配 + 单次替换**
- 命中"隐藏路径"时翻译成 `/dev/null`

**不负责**
- ❌ 与 native 同步（通过构造回调 `onAdd` / `onRemove` / `onClear` 注入）
- ❌ 具体规则内容（`setupAppRedirections` 等"规则组合"仍在 `NativeHookBridge`）
- ❌ 隐藏集合本身（通过 `isHidden` 回调查询 `NativeHookBridge.hiddenPaths`）

## 函数

| 签名 | 作用 | 副作用 |
| --- | --- | --- |
| `add(from, to)` | 加/覆盖一条规则 | 触发 `onAdd` → native |
| `remove(from)` | 删一条 | 触发 `onRemove` |
| `clear()` | 清空 | 触发 `onClear` |
| `snapshot(): Map<String,String>` | 只读快照 | 无 |
| `size(): Int` | 规则数 | 无 |
| `translate(path): String` | 翻译 | 无（纯函数） |

## 偏差登记

| # | 偏差 | 类型 | 说明 |
| --- | --- | --- | --- |
| 1 | **最长前缀匹配**（非精确匹配） | ✅ 必要 | 重定向按目录前缀生效 |
| 2 | 用 `startsWith` 判前缀、不检查目录边界 | 🟡 已知简化 | **约定：规则前缀必须带尾斜杠**（`/data/data/com.a/`），否则 `com.a2` 会被误命中 |
| 3 | 只做一次替换 | ✅ 设计如此 | 与 native `redirect_path()` 一致，避免规则互相递归 |
| 4 | `ConcurrentHashMap` | ✅ | 主线程注册、guest 线程查询 |

## 约束与坑

1. **必须与 native `redirect_path()` 语义一致**（最长前缀 + 单次替换），否则出现"Kotlin 说 A、native 做 B"的双轨不一致。
2. native 未就绪时 `onAdd/onRemove/onClear` 为空操作；规则先留在 Kotlin 侧，由 `NativeHookBridge.initNativeHooks` 补推。
3. 规则前缀**必须带尾斜杠**。
4. 已有 7 个 JVM 单测：`core/hook/src/test/.../PathRedirectRegistryTest.kt`（最长前缀优先 / 未命中 / 隐藏路径 / 不递归 / remove / clear / native 回调）。

## 相关

- `docs/代码镜像/core/hook/JavaIoHooks.md`（Java 层重定向，默认关闭）
- `docs/架构/架构-路径转换机制摸底.md`
