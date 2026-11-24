# 代码镜像：`core/virtualization/engine/LegacyMessageHandlers.kt`

> 对应代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/engine/LegacyMessageHandlers.kt`
> v151 从 `ActivityThreadHook.kt`（原 L1477–1666）迁出。

## 职责

**做什么**（三个 `internal` 扩展函数，均作用于 `ActivityThreadHook`）
- `handleLegacyLaunchActivity(msg)`：Android 9 以下的 `LAUNCH_ACTIVITY`(100) 路径，改 `ActivityClientRecord.activityInfo.name`
- `handleCreateService(msg)`：`CREATE_SERVICE`(114)，把 stub Service 的 `ServiceInfo` 换成 guest 真实 Service
- `handleReceiver(msg)`：`RECEIVER`(113)，把 stub Receiver 的 `ActivityInfo` 换成 guest 真实 Receiver

三者共同模式：反射取 framework 数据对象 → 改写 `name` / `packageName` / `applicationInfo.{packageName,sourceDir,publicSourceDir,dataDir}` → 调 `replaceClassLoader()`

**不负责**
- ❌ Activity 生命周期事务（`EXECUTE_TRANSACTION` → 未来的 `TransactionInterceptor`）
- ❌ LoadedApk 重建 / 资源注入
- ❌ 决定"要不要接管"（`ActivityThreadHook.handleMessage` 分发）

## 函数

| 签名 | 作用 | 返回 | 副作用 / 前提 |
| --- | --- | --- | --- |
| `ActivityThreadHook.handleLegacyLaunchActivity(msg: Message): Boolean` | 老版本 Activity 类名替换 | **恒 `false`**（让 original 继续） | Android 9+ 直接返回 false |
| `ActivityThreadHook.handleCreateService(msg: Message): Boolean` | stub Service → guest Service | **恒 `false`** | 会调 `replaceClassLoader`；`engine.getDataDir` 写 `applicationInfo.dataDir` |
| `ActivityThreadHook.handleReceiver(msg: Message): Boolean` | stub Receiver → guest Receiver | **恒 `false`** | 会调 `replaceClassLoader`；**不设** `dataDir`（历史行为） |

## 偏差登记

| # | 偏差 | 相对谁 | 类型 | 说明 |
| --- | --- | --- | --- | --- |
| **1** | 反射改写 framework 私有字段（`intent` / `info` / `applicationInfo` / `activityInfo`） | AOSP 不会改写 | ✅ **必要偏差** | 没有公开 API 能把 stub 组件换成 guest 组件。字段名随 OS 变化时**降级为不接管**（`return false`），不会崩 |
| **2** | 三个 handler 恒 `return false` | `Handler.Callback` 语义（`true`=已消费） | 🟡 **待确认** | 当前 `ActivityThreadHook` 的 Callback 包装**忽略返回值**，所以 `false` 不影响结果。但若修好"返回值生效"，这里要重新定义：我们只做"替换"，不接管执行 → 应仍为 `false` |
| **3** | `RECEIVER` 分支不设 `applicationInfo.dataDir`（`CREATE_SERVICE` 设了） | 两者不一致 | ⚠️ **历史不一致** | 可能是遗漏。改动前需确认是否有依赖（Receiver 通常不读 dataDir） |
| **4** | `LAUNCH_ACTIVITY` 在 9+ 直接跳过 | — | ✅ 必要 | 9+ 走 `EXECUTE_TRANSACTION`；保留为了老设备 |

## 约束与坑

1. **绝不能抛异常**：位于 `mH` 的 Callback 调用链上，抛一次会打断消息分发。所有 handler 都是 try/catch + `return false`。
2. **替换后必须调 `replaceClassLoader`**，否则类仍从 stub classloader 解析 → `ClassNotFoundException`。
3. **`msg.obj` 的结构随 OS 版本变化**（`CreateServiceData` / `ReceiverData` 字段名），任何 `findField` 都要允许返回 null。
4. **debug 诊断**：入口/命中替换/失败 都写 `debug-trace.log`（tag = `legacy`），排查时先看这几条。
5. 三个函数是**扩展函数**（`internal fun ActivityThreadHook.xxx`），依赖 `ActivityThreadHook` 把
   `engine` 与 `replaceClassLoader` 暴露为 `internal`（v151 改）。改可见性时只改修饰符，不要改名。

## 相关

- `docs/代码镜像/core/virtualization/engine/ActivityThreadHook.md`（分发与契约）
- `docs/架构/架构-文件拆分契约模式.md`（拆分原则）

## 可测性（不可纯 JVM 测，原因如下）

| 部分 | 能否单测 | 原因 |
| --- | --- | --- |
| 三个 handler 的主体 | ❌ | 全部走反射（\ndroid.app.ActivityThread\、\ActivityClientRecord\ 等），
JVM 单测里这些类不存在，反射必然失败 —— 而"失败降级"正是被测行为，无法区分"降级正确"与"环境缺失" |
| 分发入口（\handleMessage\ 的 when 分支） | ❌ | 需要构造 \ActivityThreadHook\（依赖 Context / StubRegistry / VirtualEngine） |

**当前覆盖方式**：
1. \module-test\ 编译（保证签名、可见性、扩展函数解析正确）；
2. 真机回归：Via 启动链路会走 \LAUNCH_ACTIVITY\ 分支，观察 \debug-trace.log\ 的 \legacy\ 事件。

**若将来要单测**：需要引入 Robolectric（能提供 \ActivityThread\/\Message\ 的真实行为），
或在 handler 里把"消息解析"与"反射执行"拆成两步，只测前者。

## 偏差登记（v158d 静态等价检查结果）

用 `tools/lint/check-refactor-equivalence.mjs` 对比 v151 拆分前后（`_ref` 仓库 `63346e6`），
三个 handler 都报「不等价」，逐项确认如下：

| 差异 | 是否改变业务逻辑 | 类型 |
| --- | --- | --- |
| 新增 `DebugTrace.event("legacy", ...)` | ❌ 只在 `DebugFlags` 开启时写调试日志，不改控制流 | ✅ 必要偏差（可观测性插桩） |
| `Timber.tag(TAG)` → `Timber.tag(TAG_LEGACY)` | ❌ 仅日志 tag 改名，便于按模块过滤日志 | ✅ 必要偏差 |
| 反射字段读写 / classloader 替换 / 条件判断 | 逐字相同 | — |

**为什么允许**：拆分的目的是让人能分清职责，插入可观测点属于拆分收益的一部分
（日志 tag 从 `ATHook` 细化为 `ATHook` + `legacy` 前缀）。但它确实是行为变化，
所以必须在此登记，而不能藏在"纯搬迁"的说法下。
