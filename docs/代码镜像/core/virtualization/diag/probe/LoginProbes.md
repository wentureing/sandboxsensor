# LoginProbes（core:virtualization / diag/probe）

- 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/diag/probe/LoginProbes.kt`
- 测试：`core/virtualization/src/test/java/com/nextvm/core/virtualization/diag/probe/LoginProbesTest.kt`（L1–L3）
- 来源：从 `VirtualApplicationManager` 抽出（v124/v125/v127/v134/v144/v145，B4-3b 第二批）
- 日期：2026-11-25

## 职责

**负责**：登录/人脸认证页面的**只读观测**与**两处点击包装**：
- v124 观测「我的」页登录按钮是否存在、是否有点击监听、控件统计；
- v125/v127 包装登录按钮 listener（首次点击起 32MB/1.5s 方法追踪）；
- v134 包装人脸认证按钮 listener（首次点击起 64MB/8s 追踪 + 8s 后过滤）；
- v144/v145 接收侧记录 intent 的 **key 名**（绝不记录值/输入文本）。

**不负责**：

| 不负责 | 在哪 |
| --- | --- |
| 何时调用（resumed / v124 timer） | `VirtualApplicationManager` 生命周期回调 |
| trace 的启停与过滤实现 | `diag/trace/InitTrace` |
| 点击后 15s 的 connect probe | 调用方以 `onLateDump` 注入（原为 `dumpV148ConnectProbe()`） |
| 日志通道 | `GuestBreadcrumb` + 文件 |

## 函数

| 函数 | 签名 | 作用 / 关键前提 |
| --- | --- | --- |
| `formatAuthRecv` | `(Boolean, String?, String?, List<String>, Boolean, Boolean?): String` | **纯函数**：v144 日志行 |
| `formatAuthRecv145` | `(String, String, String): String` | **纯函数**：v145 日志行 |
| `probeLoginButtonState` | `(Activity, source: String)` | 只在签名变化时写 breadcrumb；v125 包装 listener（只包一次） |
| `wrapFaceVerifyButton` | `(Activity, onLateDump: () -> Unit = {})` | 只在 `AuthenticationActivity` 上；未找到时每个 Activity 只记一次 |
| `probeAuthIntentPhone` | `(Activity)` | v144；PII-safe（只 key 名 + phone 字段是否为空） |
| `probeAuthIntentPhone145` | `(Activity)` | v145；只记录嵌套 bundle 的 key 名 |

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | 硬编码 `com.mc10086.cmcc.authentication.common.AuthenticationActivity`、`mine_login_btn` / `identity_login_tip` id、`"登录"` / `"人脸识别登录"` / `"面部识别身份认证"` 文本 | 定向排查特定 app | 必要偏差 | `v124/v134` 日志行 |
| 2 | 点击包装器改 listener（不是只观测） | 需要知道"点击是否到达 body"；只包装、异常原样 rethrow，行为不变 | 必要偏差 | `v125/v134 CLICKED` / `CLICKED returned` / `CLICKED threw` |
| 3 | `onLateDump` 由调用方注入 | 让探针不依赖容器启动链（`dumpV148ConnectProbe`） | 结构化改造（行为等价） | 调用点传 `{ dumpV148ConnectProbe() }` |
| 4 | v144/v145 只记 key 名 | 值/输入框文本是用户数据，绝不写日志 | 必要偏差 | 日志里只有 keys/bundleKeys/queryParams |

## 约束与坑

1. **必须主线程**：遍历 View 树、改 listener。
2. **只在 guest Activity 上**：`AuthenticationActivity`/登录页判断写死。
3. **包装器只包一次**：`traceCount` 静态计数；`V125ClickWrapper`/`V134ClickWrapper` 类名检查防二次包装。
4. **异常原样 rethrow**：包装器不能吞 guest 的异常（会改变行为）。
5. **v134 的 15s dump 在点击前 post**：即使原 listener 抛异常也会执行。
6. 入口不可纯 JVM 测；可测部分为 `formatAuthRecv` / `formatAuthRecv145`（L1–L3）。
