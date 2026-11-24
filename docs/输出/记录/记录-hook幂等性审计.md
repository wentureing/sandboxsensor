# 审计：是否存在「路径被反复 hook」

- 日期：2026-11-24
- 触发：用户提出「hook 通常一次就够；次数多说明职责不清或某种错误」。
- 结论：**没有发现反复 hook。** 唯一的真实缺口是 v150 拆分时我自己引入的
  `MainLooperGuard` 缺幂等保护（已修）。

---

## 一、方法

1. 写静态检查工具 `tools/lint/check-hook-idempotent.mjs`：
   扫描所有 `fun install*` / `fun hook*` 入口，检查函数体前 12 行内是否有幂等信号
   （`installed` / `initialized` / `compareAndSet` / `synchronized` / 提前 return …
2. 只报**公共入口**（private 的内部辅助函数通常只被单一入口调用一次）。
3. 对每个命中项**人工核实**真实幂等性。

## 二、扫描结果（11 个公共入口）

| 入口 | 工具判断 | **实际幂等性** | 结论 |
| --- | --- | --- | --- |
| `SystemServiceProxyManager.installDeferredProxies` | 缺保护 | ✅ **自幂等**：`run()` 后 `iterator.remove()`，二次调用 map 已空 | 误报 |
| `JavaIoHooks.install` | 缺保护 | ✅ 有 `installed`（`installed = ok` 形式，正则未匹配） | 误报 |
| `ProcessKillGuard.install` | 缺保护 | ✅ `ThreadLocal` + `is` 判断 | 误报 |
| `ActivityThreadHook.installInstrumentationHookEarly` | 缺保护 | ✅ 内部靠 `instrumentationHooked` 标志 | 误报 |
| `HookEngine.hookStaticField` / `hookInstanceField` | 缺保护 | ✅ **参数化**：对不同字段装 hook，本就该多次调用 | 正常 |
| `VirtualContentProviderManager.installProvider` | 缺保护 | ✅ **参数化**：对不同 Provider 装 | 正常 |
| `AntiDetectionEngine.hookRootChecks/hookEmulatorChecks/hookXposedChecks` | 缺保护 | ⚪ 零调用（未来能力） | 暂不处理 |
| `SystemServiceProxyManager.installAllProxies` | 缺保护 | ⚠️ 确实无保护，但**调用点唯一**（`VirtualEngine.initialize`，每进程一次） | 可接受 |
| **`MainLooperGuard.install`** | 缺保护 | ❌ **真实缺口**：重复调用会嵌套 `Looper.loop()` → 同一条消息被分发两次 | **已修（v157）** |

## 三、已修

`MainLooperGuard` 加 `AtomicBoolean` 幂等：

```kotlin
private val installed = AtomicBoolean(false)

fun install() {
    if (!installed.compareAndSet(false, true)) {
        Timber.w("已安装，跳过重复 install（避免嵌套 looper）")
        DebugTrace.event("guard", "duplicate install blocked")
        return
    }
    ...
}
```

重复安装时会写 `debug-trace.log`（tag=`guard`，`duplicate install blocked`），可观测。

## 四、工具的使用建议

```powershell
node tools/lint/check-hook-idempotent.mjs _ref/ShitJumpingVM/core
```

**它是"提示"而不是"门禁"**（`exit 0`），因为大量命中是：
- 参数化入口（`hookStaticField(字段)`）—— 本来就该多次调用
- 自幂等实现（`run` 后 `remove`、有 `installed` 标志）—— 正则难以穷举

**人工判断的三条标准**：

| 判定 | 依据 |
| --- | --- |
| **正常** | 参数化（每次传不同目标） |
| **正常** | 自幂等（执行后清理、或有标志位） |
| **需要修** | 无参数、无标志、且会被多个调用点/循环调用 → **会重复 hook** |

## 五、附带发现（值得后续跟进）

| # | 发现 | 影响 |
| --- | --- | --- |
| 1 | `SystemServiceProxyManager.installAllProxies` 无幂等保护 | 当前调用点唯一，风险低；但若将来在多处调用（如多实例场景）会重复装代理 → 建议加标志位 |
| 2 | 20+ 个 `installXxxProxy` 是 private 内部辅助 | 不需要单独保护，但**它们没有"已装"校验** —— 完全依赖 `installAllProxies` 只被调一次 |
| 3 | 运行时可观测性 | `debug-trace.log` 里已有 `initNativeHooks enter`、`javaIo installed=`、`guard` 等事件，可据此确认"每进程只装一次" |

## 六、结论

> **当前容器不存在"路径被反复 hook"的问题。**
> 静态扫描到的 11 处公共入口，10 处是参数化/自幂等/零调用，1 处（`installAllProxies`）
> 虽然无保护但调用点唯一。
>
> 真正修掉的是 **v150 拆分时引入的 `MainLooperGuard` 缺口** —— 这也说明
> 「拆分后必须复查被搬走代码的守卫逻辑有没有一起搬」。
