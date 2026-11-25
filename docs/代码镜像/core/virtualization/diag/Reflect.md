# Reflect（core:virtualization / diag）

- 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/diag/Reflect.kt`
- 日期：2026-11-25

## 职责

**负责**：一个反射辅助 —— 沿类继承链取字段并设为可访问。

**不负责**：字段缓存、异常包装、可见性绕过（hidden API 由 `AndroidCompat` 处理）。

**为什么单独成文件**：`VirtualApplicationManager` 与 `HijackProbes` 曾各有一份实现，
且**语义不同**（一份遍历父类、一份只查本类）——不统一迟早分叉。

## 函数

| 函数 | 签名 | 作用 / 关键前提 |
| --- | --- | --- |
| `fieldOf` | `(Class<*>, name: String): Field` | 从 `cls` 开始沿 `superclass` 查找 `getDeclaredField`，找到即 `isAccessible = true`；找不到抛 `NoSuchFieldException`（带类名） |

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | 遍历父类（而非只查本类） | 与 `VirtualApplicationManager` 原实现一致；隐藏字段常在父类（如 `Activity.mInstrumentation`） | 必要偏差 | 取不到时抛异常，调用方 catch 成日志行 |
| 2 | 不做缓存 | 诊断路径调用频率低；缓存会引入"类加载后字段变化"的隐蔽 bug | 中性偏差 | 无 |

## 约束与坑

1. **不要改成只查本类**：`HijackProbes` 新写的副本曾漏掉父类遍历，统一时必须取原类的语义。
2. **不吞异常**：找不到就抛，由调用方决定是否降级成日志（诊断代码都 catch）。
3. 设置 `isAccessible = true` 在 Android 上仍可能被 hidden API 策略拦；那属于 `AndroidCompat` 的职责。
