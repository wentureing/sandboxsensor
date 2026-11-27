# 方案：ActivityThreadHook 与 Binder hook 的语义等价重构

- 日期：2026-11-24
- 触发：用户指出「**零调用也要保留（未来增长点）**」，并要求重点重构
  **ActivityThreadHook** 与 **Binder hook** 的语义；**先不提交**。
- 本文只做分析 + 方案，**未修改任何代码、未提交**。

---

## 零、零调用能力的处置（结论：保留）

| 能力 | 处置 | 理由 |
| --- | --- | --- |
| `AntiDetectionEngine`（805 行） | **保留** | 反检测是长期方向；接线前需先修它自带的 2 个缺陷（`is_path_hidden` 精确匹配、无 `readdir` 过滤） |
| `IdentitySpoofingEngine` | **保留** | 设备指纹伪装是产品化必需 |
| `FileAccessInterceptor`（~100 行） | **保留** | 可作为"Java 层重定向"的参考实现与回归对照；v148 的 native 实现可以拿它做行为比对 |

> 唯一要求：在这三处类注释里标明「**当前零调用，为未来能力预留**」，避免后人误判为死代码删除。

---

## 一、ActivityThreadHook：三处语义不等价

文件：`core/virtualization/src/main/java/com/nextvm/core/virtualization/engine/ActivityThreadHook.kt`（2133 行）

### 1.1 【明确 bug】`handleMessage()` 的返回值被忽略

**位置**：L118-127（`Handler.Callback` 包装）

```kotlin
callbackField.set(mH, Handler.Callback { msg ->
    try {
        handleMessage(msg)          // ← 返回值（Boolean）被丢弃
    } catch (e: Exception) {
        Timber.tag(TAG).e(e, "Error in hook callback for msg.what=${msg.what}")
    }
    // Return false to let the original handler also process the message
    originalCallback?.handleMessage(msg) ?: false    // ← 总是调用 original
})
```

**不等价点**（维度 4：时序/重复处理）

- `handleMessage()` 内部有分支返回 `true`（表示"我已处理"），例如
  `handleExecuteTransaction()` → `handleLaunchActivityItem()` 成功时返回 `true`。
- 但外层**无条件**再调 `originalCallback.handleMessage(msg)` → **同一条消息被处理两次**。
- `handleLaunchActivityItem` 里会替换 classloader / 注入 `ActivityClientRecord`，
  若随后 original 再处理一遍，可能出现：Activity 被创建两次、`ActivityClientRecord`
  状态互相覆盖、或者"启动成功但界面空白"。

**破坏表现**

- 偶发"Activity 启动两次"（`onCreate` 被调两次）
- 或者"记录已被替换、original 又按 stub 处理" → 界面显示 stub 而非 guest
- 这类问题极难归因，因为它取决于 `originalCallback` 是否为 null、消息类型、时序

**重构要点**

```kotlin
// 正确语义：我们的 hook 若"已处理"，就不再交给 original
val handled = try { handleMessage(msg) } catch (e: Exception) { false }
if (handled) return true
originalCallback?.handleMessage(msg) ?: false
```

**并且**要逐个复核 `handleMessage` 各分支的返回值语义：
`EXECUTE_TRANSACTION` / `LAUNCH_ACTIVITY` / `CREATE_SERVICE` / `RECEIVER`
—— 哪些是"已完全接管"，哪些是"只做预处理，仍应交回 original"。

**验证**

| 判据 | 方法 |
| --- | --- |
| 不重复创建 | 探针/应用里在 `Activity.onCreate` 打点，统计次数必须为 1 |
| 不显示 stub | 启动 Via 后 `dumpsys activity` 的 Activity 类名应为 `mark.via.Shell` |
| 无回归 | Via 可交互 + 探针全量 |

### 1.2 【架构风险】主 looper 被嵌套包装

**位置**：L147-221 `installMainLooperCrashGuard()`

```kotlin
Handler(Looper.getMainLooper()).post {
    while (true) {
        try { Looper.loop() } catch (e: Throwable) { ... }
    }
}
```

**不等价点**（维度 4/7：时序、重入）

- AOSP 的 `ActivityThread.main()` **已经调用了一次 `Looper.loop()`**，
  这里在它的消息回调里**再次调用 `Looper.loop()`** → 形成嵌套 looper。
- 异常处理路径被拉长：`catch` 里 `throw e` 之后，异常要穿过
  嵌套的 looper 才能到达 `Thread.uncaughtExceptionHandler`，栈信息与
  AOSP 原生崩溃**不一致**（这也解释了为什么本项目多次误判崩溃原因）。

**当前实现的可取之处**（要保留）

- 它**没有无条件吞异常**：L210-213 明确 `throw e` 保留正常崩溃行为；
- 只吞两类窄场景：Toast UID mismatch、GMS "Unknown calling package name"。

**重构要点**

| 方向 | 说明 |
| --- | --- |
| A. 不替换 looper，改用 `Thread.setDefaultUncaughtExceptionHandler` | 治本：只处理"未捕获异常"，不动 looper 结构。可精确判断异常类型后决定"记录并吞"或"交回原 handler" |
| B. 若必须保留 looper 包装 | 至少保证：① 只包一层（幂等）；② 崩溃时**完整打印原始栈**到 breadcrumb；③ 记录被吞的异常数（可观测） |
| C. 明确"吞异常"的白名单 | 现在靠 message 字符串匹配（`"Adding window failed"` / `"not in UID"`）—— 属于**脆弱耦合**，OS 版本升级会失效，需要加"未匹配到 → 按崩溃处理"的兜底（已有 ✓ 要保留） |

**验证**

- 故意在 guest 主线程抛未捕获异常 → 应能拿到**与真机一致**的崩溃栈
- Toast UID mismatch 场景仍被吞（不崩）且 breadcrumb 有记录

### 1.3 `handleExecuteTransaction` 的异常兜底过宽

**位置**：L323-325

```kotlin
} catch (e: Exception) {
    Timber.tag(TAG).e(e, "Error handling EXECUTE_TRANSACTION")
}
return false
```

**不等价点**（维度 3："失败"与"不存在"混同）

- 任何异常（包括反射字段改名、`ClientTransaction` 结构变化）都被吞成 `return false`
  → **original 照常处理**，于是"我们的接管静默失效"，表现为 stub 被启动 / 界面空白。
- 正确做法：区分「结构探测失败（可容忍）」与「接管失败（必须记录 + 显式暴露）」。

**重构要点**

- 反射失败 → `DebugTrace.fail` + breadcrumb 记一条 `v1xx-transaction-shape-changed`
- 接管失败 → 记 fail 并**可选地**抛出让问题暴露（受开关控制）

---

## 二、Binder hook：两处语义不等价

### 2.1 【高风险】吞掉所有 `SecurityException`

**位置**：`core/binder/.../proxy/ActivityManagerProxy.kt` L241-254

```kotlin
} catch (e: Exception) {
    val cause = ...
    if (cause is SecurityException) {
        // SecurityException from AMS = UID/package mismatch in virtual env.
        Timber.tag(TAG).w("AMS SecurityException swallowed for $methodName: ${cause.message}")
        // 返回 null
    }
}
```

**不等价点**（维度 1/3：异常类型与含义）

- AOSP 的 `IActivityManager` 抛 `SecurityException` 有**多种真实含义**：
  权限不足、调用方不允许、组件未导出、后台启动限制……
- 这里把**所有** `SecurityException` 都当成"虚拟环境的 UID mismatch"吞掉 →
  应用以为调用成功 → 后续状态全部错乱（例如以为 Activity 已启动、以为广播已发出）。

**破坏表现**

- "点了没反应但没报错"
- 应用的权限分支逻辑走错（本该提示"无权限"，结果静默）

**重构要点**

| 步骤 | 内容 |
| --- | --- |
| 1 | 只吞**可证明**是虚拟环境导致的（如 message 含特定 UID/包名不匹配，且目标包是虚拟包） |
| 2 | 其余 `SecurityException` **原样抛出**（保持维度 1 等价） |
| 3 | 被吞的每一条都要 `DebugTrace.fail` 落盘（可观测），并在统计里计数 |

**同类位置**：L835-838（GMS `bindService` 吞 SecurityException）—— 同样处理。

### 2.2 `getPackageInfo` 返回的 `PackageInfo` 字段不完整

**位置**：`core/binder/.../proxy/PackageManagerProxy.kt` L217-238 / `handleGetPackageInfo`

**不等价点**（维度 6：对象完整性）

- 对虚拟包返回 `virtualPm.getPackageInfo(...)` 构造的对象；对真机安装的同名包返回原值。
- 风险字段（应用常读，且可能为 null/默认值）：
  `applicationInfo.sourceDir` / `publicSourceDir` / `nativeLibraryDir` / `dataDir`、
  `splitNames` / `splitSourceDirs`、`signatures` / `signingInfo`、
  `versionCode` / `longVersionCode`、`sharedUserId`、`targetSdkVersion`、
  `applicationInfo.metaData`

**破坏表现**

- `NullPointerException`（读 `signingInfo`）
- 分包应用（`splitNames` 为空）加载失败
- 签名校验类 SDK 直接判为"被篡改"

**重构要点**

- 建一张 **字段对照表**：字段 → 真机值来源 → 虚拟环境取值 → 是否允许默认值
- 对"应用一定会读"的字段（sourceDir/signingInfo/targetSdkVersion）必须填真实或等价物
- 每加一个字段都要在探针里加一条断言

---

## 三、重构批次与判据

| 批次 | 内容 | 风险 | 为什么这个顺序 |
| --- | --- | --- | --- |
| **B1** | 1.1 返回值忽略（明确 bug） | 低 | 改动 3 行，收益明确，且可能是多个"诡异现象"的根源 |
| **B2** | 2.1 吞 SecurityException 收窄 | 中 | 直接影响"错误可见性"，与 B1 同源（都是"静默"问题） |
| **B3** | 1.3 事务兜底区分容忍/失败 | 低 | 纯诊断，不改控制流 |
| **B4** | 1.2 looper 换实现（方案 A） | 高 | 动主线程结构，必须在 webview/mime 问题收口后做 |
| **B5** | 2.2 PackageInfo 字段补齐 | 中–高 | 需要逐字段验证，工作量大但边界清晰 |

**每批次都必须产出（规则 7）**

| 产物 | 位置 |
| --- | --- |
| 变更计划 | `docs/输出/变更/<YYYYMMDD>-<批次>.md` |
| **代码镜像文档** | `docs/代码镜像/<与代码一致的路径>.md`（职责/函数/约束与坑） |
| AOSP 对照表 | 该批次涉及的每个 API 的「异常/返回值/副作用/时序」四项对照 |
| 失败路径探针 | 只走失败路径的用例（v141 教训） |
| 真实应用回归 | Via 可交互 + 探针全量 |

---

## 四、当前状态

| 项 | 状态 |
| --- | --- |
| 代码 | **未修改** |
| 提交 | **未提交**（按用户要求） |
| 文档 | 本文件（方案）已落盘 |
| 下一步 | 等用户确认批次与优先级后，按 B1→B2→B3 顺序落"变更计划"再改 |
