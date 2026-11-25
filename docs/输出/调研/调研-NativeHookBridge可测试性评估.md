# 调研：NativeHookBridge 的可测试性评估（拆 B1 之前）

- 日期：2026-11-25
- **核实基准**：容器 `baseline-v150 @ 08e9369`；CI `45581783274` 绿
- 对象：`core/hook/src/main/java/com/nextvm/core/hook/NativeHookBridge.kt`
  —— **1231 行、2 个类、51 个公开函数**（另一篇文档记的 1084 行是早期快照，已过时）
- 结论先给：**可测性中等偏上（约 34/51 可纯 JVM 测），但被"混在一个文件里"拖累**；
  拆解**不会提高可测比例**，它提高的是"边界清晰度"与"纯逻辑能否独立成层"。

---

## 一、文件实际结构（先纠正一个事实）

```
NativeHookBridge.kt (1231 行)
├── class NativeHookBridge @Inject constructor()        ← 42–1122 行
│   ├── 规则/隐藏/fake/属性 状态机（纯）
│   ├── translatePath / setupApp / removeApp …（纯 + native 调用）
│   ├── initNativeHooks(Context) / spoofProcSelf / setProcessName …（Android/native）
│   └── 15 个 private external（JNI）
└── class FileAccessInterceptor(bridge: NativeHookBridge)  ← 1130–1231 行
    └── interceptFile / FileInputStream / FileOutputStream / exists / canRead / listFiles
        + readFilteredProcMaps（纯 java.io + Timber）
```

**一个文件两个类**本身就是职责混杂的证据；但 `FileAccessInterceptor` 通过**构造注入**
`bridge` —— 这是本文件里唯一"天然可测"的边界，值得保留这个形态。

## 二、可测试性矩阵

| 组 | 代表函数 | 数量 | 依赖 | 现在能纯 JVM 测吗 | 现有覆盖 |
| --- | --- | --- | --- | --- | --- |
| **A 纯状态机** | `add/remove/clear/snapshot/count`、`hide/unhide/isPathHidden/count`、`fake content ×4`、`property ×2`、`translatePath`、`setupApp/External/PathFidelity`、`removeApp/External/PathFidelity` | ~25 | 无（native 调用被 `nativeHooksAvailable=false` 短路） | ✅ **能** | ✅ 55 例（T1–T43、R1–R12、K/H/W） |
| **B 纯字符串构造** | `filterProcMaps`、`buildFakeProcStatus`（**private**）、`spoofSystemProperties` | 3 | 无 | ⚠️ 前两个可测，`buildFakeProcStatus` 被 `private` 挡住 | 0 |
| **C 文件拦截** | `FileAccessInterceptor` 的 6 个 + `readFilteredProcMaps` | 7 | `java.io`（临时目录）；后者要 `/proc/self/maps` | ✅ 6 个可测；`readFilteredProcMaps` 只能测异常分支 | 0 |
| **D native/Android** | `initialize`、`hookRuntimeNativeLoad`、`initNativeHooks(Context)`、`cleanup`、`spoofProcSelf`、`setProcSpoofSuspended`、`setProcessName`、`armSeccompDiag`、`installJavaIoHooks`、`hookFileClass`、`hookRuntimeExec`、`hookSystemProperties`、`dumpFsStats`、`isNativeAvailable`、`getNativeRedirectCount` + 15 个 `external` | ~16 公开 + 15 external | `Context` / JNI / `android.app.Application` | ❌ **不能** | 0 |

**比例**：可纯 JVM 测 ≈ **34/51（67%）**；已覆盖 25/34（≈74%），全部集中在 A 组。

## 三、真正的可测性瓶颈（不是文件大小）

| # | 瓶颈 | 影响 | 修法 |
| --- | --- | --- | --- |
| 1 | `initNativeHooks(context: android.content.Context?)` 和 `android.app.Application.getProcessName()` 让**整个类**带 Android 依赖 | A 组虽然能测（AGP 提供 stub），但**不能下移到 `core:model`**，也不能脱离 Android 单元测试环境独立跑 | 把 A 组抽成纯类 `PathRuleStore`（无 `android.*` import） |
| 2 | `buildFakeProcStatus` / `filterProcMaps` 是 `private` | 纯逻辑却测不到 | 改 `internal`（规则 9：可见性变更要记录 `--deps` 结果） |
| 3 | `FileAccessInterceptor` 与 `NativeHookBridge` 同文件 | 阅读/排障时要在 native 声明与工具类之间跳 | 独立成文件（本来已是独立类，零语义变化） |
| 4 | `readFilteredProcMaps` 读 `/proc/self/maps` | 只能在 Linux/真机验证 | 保留在 C 组，文档标注"真机项" |

## 四、拆解后会发生什么（收益要诚实）

| 维度 | 拆前 | 拆后 |
| --- | --- | --- |
| 可测比例 | 67%（34/51） | **仍然 67%** —— 拆解不创造可测性 |
| 已覆盖比例 | A 组 ~74% | 可把 A+B+C 补到 **100%**（b+补 B/C 两个纯组） |
| 边界清晰 | 一个文件 2 个类、5 类职责 | 3 个文件各 1 职责：`PathRuleStore`（纯）/ `FileAccessInterceptor`（纯 java.io）/ `NativeHookBridge`（native 桥） |
| 纯逻辑能否下移 | ❌（带 Android import） | ✅ `PathRuleStore` 可移到 `core:model`，与 `PathKinds`/`PathPolicy` 同层 |
| 排障 | 在 1231 行里找 | 「翻译不对」→ store；「读写没拦截」→ interceptor；「hook 没装上」→ bridge |

**所以拆 B1 的正当理由不是"可测性提高"，而是**：
① `PathRuleStore` 消除 Android 依赖后可下移、可被更多模块复用；
② native 桥与纯逻辑分离，排障路径单一；
③ 纯组可补到 100% 覆盖，而 native 组明确"只能真机"。

## 五、建议的拆解形态（3 个文件，2 个前置改动）

```
core/hook/.../hook/
├── PathRuleStore.kt           ← 纯：规则表/隐藏/fake/属性/translatePath/setup*/remove*
├── FileAccessInterceptor.kt   ← 纯 java.io：intercept*（构造注入 PathRuleStore）
└── NativeHookBridge.kt        ← native 桥：initNativeHooks/Context、spoofProcSelf、
                                  installJavaIoHooks、外部 JNI 声明、委托给 store
```

| 前置改动 | 内容 | 依据 |
| --- | --- | --- |
| P1 | 把 `initNativeHooks` 里对 `android.app.Application.getProcessName()` 的调用改成**参数传入进程名** | 让 store 无 Android 依赖 |
| P2 | `buildFakeProcStatus`、`filterProcMaps` 改 `internal` | 可测纯逻辑；记录 `--deps` |
| P3 | `NativeHookBridge` 对外的 43 个 API 保持**签名不变**，内部委托 store | 调用点零改动（与 S1 同一策略） |

## 六、本次评估带出的顺带事实

- 本文件早期文档记"1067 行"、后记"1084 行"，**实际 1231 行** —— 再次印证"文档时间不可信，
  以代码为准"（`docs/架构/README.md` §零）。
- `FileAccessInterceptor` 的 fake content 分支用 `File.createTempFile`（纯 java.io）→ 可测，
  但**临时文件不会被清理**（只 `deleteOnExit`）→ 属"待观察"，不属本次范围。
