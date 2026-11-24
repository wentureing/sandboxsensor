# 代码镜像：`core/virtualization/lifecycle/CustomerProbes.kt`

> 对应代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/CustomerProbes.kt`
> v153 从 `VirtualApplicationManager.kt`（原 L3128–3619）整块迁出，491 行代码 + KDoc/import。

## 职责

**做什么**（都是 `internal` 扩展函数，作用于 `VirtualApplicationManager`）
- `probeAuthIntentPhone(a)` / `probeAuthIntentPhone145(a)`：CMCC 认证 Activity 的 Intent 探测
- `wrapFaceVerifyButton(a)`：人脸核验按钮包装
- `probeLoginButtonState(a, phase)`：登录按钮状态采样（`phase` = `resumed` / `timer`）
- `v134ReportedActivities`：按 Activity identity 去重的集合

**不负责**
- ❌ guest 应用启动 / LoadedApk 注入（`VirtualApplicationManager` / 未来的 `GuestBootSupport`）
- ❌ 路径重定向（`GuestPathSetup`）
- ❌ 通用诊断（breadcrumb / stack dump / memory dex）
- ❌ 决定"要不要探测"（调用点在 `VirtualApplicationManager` 的 Activity 生命周期回调里）

## 函数

| 签名 | 作用 | 副作用 |
| --- | --- | --- |
| `VirtualApplicationManager.probeAuthIntentPhone(a: Activity)` | 只对 `com.mc10086.cmcc.authentication.common.AuthenticationActivity` 生效，采样其 Intent | 只写日志 |
| `VirtualApplicationManager.probeAuthIntentPhone145(a: Activity)` | 同上（v145 版本，另一套采样字段） | 只写日志 |
| `VirtualApplicationManager.wrapFaceVerifyButton(a: Activity)` | 遍历视图树，给"人脸核验"按钮加包装监听 | **会修改 View**（加 listener） |
| `VirtualApplicationManager.probeLoginButtonState(a: Activity, phase: String)` | 遍历视图树统计可点击控件数量/状态 | 只写日志；用 `v134ReportedActivities` 去重 |
| `v134ReportedActivities: MutableSet<Int>` | 避免同一 Activity 重复报告 | 跨调用共享状态 |

## 偏差登记

| # | 偏差 | 相对谁 | 类型 | 说明 |
| --- | --- | --- | --- | --- |
| **1** | **硬编码客户类名**（`com.mc10086.cmcc.authentication.common.AuthenticationActivity`） | — | ⚠️ **临时实现（技术债）** | 为单个客户写的探测写在容器核心代码里，会随客户数量无限膨胀。**建议**：改成"按包名的探测插件表"，或整体移入 debug 构建 |
| **2** | 探测只观测、不改行为（`wrapFaceVerifyButton` 除外） | — | ✅ 安全 | 全部 try/catch；失败不影响 guest 生命周期 |
| **3** | release 构建也会包含 | — | 🟡 **待评估** | 目前只是运行时不触发（靠类名不匹配），不是编译期裁剪。后续可用 `BuildConfig.DEBUG` 门控或在 `app/build.gradle.kts` 里做 source set 隔离 |
| **4** | `wrapFaceVerifyButton` 会挂 listener | AOSP 不会 | ⚠️ **需确认必要性** | 这是唯一"改行为"的探测。若只是为了观测，应改成纯读；若确实要拦截点击，要在文档里写明原因 |

## 约束与坑

1. **绝不能抛**：挂在 Activity 生命周期回调上，抛一次会中断 `onResume`。
2. 函数是扩展函数，依赖 `VirtualApplicationManager` 把 `v134ReportedActivities` 暴露为 `internal`（v153 改，只改修饰符）。
3. **换客户就要改代码**：改之前确认是否还有客户在用；长期应改为配置驱动。
4. 调试输出目前混用 `Timber` 与 `DebugTrace`；统一时应改用 `DebugTrace`（tag = `customer`）。
5. 调用点集中在 `VirtualApplicationManager` 的两处（`onActivityResumed` 回调与定时器），共 8 处。

## 可测性（待补）

| 可测部分 | 方式 |
| --- | --- |
| 类名匹配判断 | 抽成 `internal fun isCmccAuthActivity(className: String): Boolean` → 纯 JVM 单测 |
| 视图树遍历 | 需要 Robolectric（`Activity` + `ViewGroup`） |
| 去重集合 | 纯逻辑，可单测 |

## 相关

- `docs/架构/架构-文件拆分契约模式.md`
- `docs/代码镜像/core/virtualization/lifecycle/VirtualApplicationManager.md`（待补：契约文件本体）

## 可测性（已落实）

| 可测部分 | 方式 | 用例 |
| --- | --- | --- |
| 客户类名判断 \isCmccAuthActivity(className)\ | 纯函数，已抽出 | \CustomerProbesTest\ 3 例（只认 CMCC / 其它不认 / 前缀相同也不认） |
| 视图树遍历、按钮包装 | ❌ 需要真实 Activity + ViewGroup | 目前只能真机回归；若要单测需引入 Robolectric |

**已抽出的纯函数**：\isCmccAuthActivity(className: String): Boolean\（v158，替换掉 3 处硬编码类名比较）。
