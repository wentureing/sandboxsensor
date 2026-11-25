# 变更：B1 拆 NativeHookBridge（两个职责）

- 日期：2026-11-25
- 状态：**执行中**（分支 `split/b1-nativehookbridge`，待 `module-test :core:hook`）
- 依据：`docs/输出/调研/调研-NativeHookBridge可测试性评估.md`、`计划-大文件拆包与职责导出.md` §零门禁
- 分级：**A 类（重构证据）**；性质：纯结构调整，行为不变

---

## 一、目标：只导出两个职责

| 职责 | 去向 | 内容 |
| --- | --- | --- |
| **① 路径规则状态机**（纯） | 新增 `PathRuleStore.kt` | 规则表 / 隐藏集合 / fake content / 属性覆盖 + `translate()`；**无 Android / Timber / DebugTrace / native 依赖** |
| **② Java 文件拦截**（纯 java.io） | 独立 `FileAccessInterceptor.kt` | 原本就在同文件的第二个类，移出即可 |

`NativeHookBridge.kt` 保留：JNI 声明、`initNativeHooks(Context)`、native 同步、proc 伪造、
日志与限流、以及 43 个对外 API 的**薄委托**。

## 二、硬约束（违反即不可运行）

| # | 约束 | 理由 |
| --- | --- | --- |
| 1 | **`external` 声明不得搬家** | C++ 符号名 = `Java_com_nextvm_core_hook_NativeHookBridge_native*`；搬到 `PathRuleStore` 会变成 `..._PathRuleStore_...` → `UnsatisfiedLinkError` |
| 2 | 对外 43 个 API 签名不变 | 否则 `VirtualApplicationManager`/`VirtualContext` 等调用点要一起改，拆变改 |
| 3 | debug 面不变 | 见 §三 |

## 三、行为等价的四个论据（debug 面专项）

| # | 原行为 | 拆后 | 验证 |
| --- | --- | --- | --- |
| 1 | hidden 命中**不记**翻译日志 | `PathRuleStore.translate()` 返回 `hiddenHit=true`，门面据此跳过 `logPathTranslation` | `PathRuleStoreTest.S6` |
| 2 | 翻译日志限流 200 条（`pathLogCount` 在门面） | 计数仍在 `NativeHookBridge`，store 不参与 | 代码审阅（未移出） |
| 3 | `dumpPathRedirections` 输出 `path rules=N` + 排序后的规则 | 门面读 `store.snapshot()` 后同样格式化 | 代码审阅 |
| 4 | `initNativeHooks` 的 `rules=${pathRedirections.size}` 与 `take(20)` 明细 | 改为 `store.count()` / `store.snapshot().entries.take(20)` | 代码审阅 |

## 四、影响范围

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `core/hook/.../PathRuleStore.kt` | **新增** | 纯状态机（~190 行） |
| `core/hook/.../FileAccessInterceptor.kt` | **新增** | 从 `NativeHookBridge.kt` 移出的第二个类（114 行） |
| `core/hook/.../NativeHookBridge.kt` | 修改 | 4 个状态字段 → `store`；17 个函数体改委托；`cleanup` 用 `store.clearAll()`；行数 1231 → ~1080 |
| `core/hook/src/test/.../PathRuleStoreTest.kt` | **新增** | S1–S8（门禁要求的 ≥1 个新测试） |

## 五、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 漏改某个状态引用 → 编译错 | `kotlin-lint --deps` / `check-imports` 已通过；grep 确认 4 个字段零残留 |
| 2 | 日志内容被改变（违反"不得破坏 debug"） | §三 四条等价论据 + 真机 smoke（延后到出包时） |
| 3 | `FileAccessInterceptor` 依赖 `NativeHookBridge.PROC_SELF_MAPS` | 该常量是 `internal const val`，同模块可见（已核实） |

## 六、回退

```powershell
git -C _ref/ShitJumpingVM checkout baseline-v150
git -C _ref/ShitJumpingVM branch -D split/b1-nativehookbridge
```

## 七、验证（按用户要求：先拆到有把握再出包）

| 阶段 | 判据 | 状态 |
| --- | --- | --- |
| 静态 | `kotlin-lint`（13 文件）+ `check-imports` + `--deps` | ✅ 通过 |
| 单测 | `module-test :core:hook`（含新增 S1–S8、既有 63 例） | 待跑 |
| C++ | `cpp-core-test`（并行 job，验证未被波及） | 待跑 |
| 产物 | `container-build` + 装包 smoke（8 项） | **延后**（用户：拆到有把握再出包） |

## 八、实际变更

（CI 通过后补）
