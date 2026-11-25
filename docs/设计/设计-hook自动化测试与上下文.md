# 设计：hook 的自动化测试 + 成功/失败上下文（只设计，不执行）

- 日期：2026-11-24
- 触发：用户要求「hook 也能在自动化环境测试」+「hook 成功失败都要求附带部分上下文」
- 依据：[Legacy Code Seams](https://github.com/wondelai/skills/blob/c172996495bed0fcd26896a9416b2093fd7073f0/working-with-legacy-code/references/seams.md)
  + 本项目 [`现状-hook验证手段矩阵`](../现状/现状-hook验证手段矩阵.md)

---

## 一、调研：hook 能在自动化环境测什么

### 1.1 把 hook 拆成四层，可测性差异极大

| 层 | 内容 | 可测性 | 手段 |
| --- | --- | --- | --- |
| **L1 决策** | 要不要 hook、hook 到哪个目标、规则内容是什么 | ✅ **完全可测** | 纯 JVM（抽纯函数） |
| **L2 状态** | hook 后的内部状态（规则表、代理记录、伪造内容、计数） | ✅ **完全可测** | 纯 JVM |
| **L3 执行 · Java** | 反射替换字段、动态代理注入、`RegisterNatives` | ⚠️ **部分可测** | Robolectric（真实 Android 类） |
| **L4 执行 · native** | GOT/PLT patch、syscall、`__system_property_get` | ❌ **不可测** | 只能真机 |

**现在的症结**：hook 的 L1–L4 混在一个 `install()` 里（一次做完），
所以"要么整套跑（要真机），要么什么都测不到"。

### 1.2 三条可行路线

| 路线 | 做法 | 能测 | 成本 |
| --- | --- | --- | --- |
| **A. 抽接缝**（推荐） | 把 `install()` 拆成 `plan()`（L1+L2，可测）+ `apply(plan)`（L3+L4，不可测） | 决策与状态 | 中（每个 hook 改结构） |
| **B. Robolectric 直测** | 在 Robolectric 里真实执行 Java 层 hook（替换 `sCache`、`mClassLoader`） | L3 的 Java 部分 | 中（需造 Android 对象） |
| **C. 契约测试** | 定义"成功时 X 必成立、失败时 Y 必成立"的契约，用假对象验证 | L1+L2+L3 的判定部分 | 低 |

**推荐组合：A + B**。A 让所有 hook 都有可测部分，B 让 Java 层 hook 有真实执行验证。

### 1.3 明确不可自动化

| 内容 | 为什么 |
| --- | --- |
| native GOT patch 是否真的生效 | 需要真实 `libc` / `dl_iterate_phdr` |
| `syscall` 是否被拦 | Robolectric 无内核 |
| 跨进程（`:p0` vs guest） | Robolectric 单进程 |
| isolated 进程（WebView renderer） | 无手段 |

**对这些，自动化只能验证"决策正确"（要不要装、装什么），**真实生效只能真机**。

---

## 二、设计：`HookCheck` —— 成功/失败都带上下文

### 2.1 问题

当前日志的问题：

```
initNativeHooks installing hooks        ← 只证明代码跑到这行
javaIo installed=true                   ← 只证明函数返回 true
```

**三个盲区**：
1. 挂到了**错误的对象**（如挂到 host 而不是 guest）时，日志一样是 `installed=true`
2. 不知道**装了多少东西**（规则 0 条也算成功）
3. 失败时不知道**为什么**（找不到符号 / 目标不存在 / 权限）

### 2.2 数据结构

```kotlin
data class HookCheck(
    val name: String,                              // nativeGOT / serviceProxy / classLoader / javaIO …
    val ok: Boolean,
    val target: String,                            // 被 hook 的对象，如 "ServiceManager.sCache"
    val expected: String,                          // 期望状态
    val actual: String,                            // 实际状态
    val context: Map<String, String> = emptyMap(), // 附加上下文（见 2.4）
    val reason: String? = null                     // 失败原因（ok=false 时必填）
)
```

**设计要点**：
- `expected` / `actual` 是**成对**的 —— 没有它就无法发现"挂错对象"
- `context` 用 `Map` 而不是字符串拼接 —— 便于结构化 diff
- `reason` 是**枚举语义**（`no_symbol` / `target_null` / `permission_denied` / `threw`），
  便于统计"最常见的失败原因"

### 2.3 输出格式（一行一条，`hookcheck` tag）

```
hookcheck name=nativeGOT    ok=true  target=libc.open        expected=hooked actual=hooked   libs=3 rules=11
hookcheck name=javaIO       ok=false target=libcore.Linux.open expected=native actual=missing reason=no_symbol_in_libjavacore
hookcheck name=serviceProxy ok=true  target=ServiceManager.sCache service=activity expected=proxy actual=ActivityManagerProxy count=14
hookcheck name=binderCache  ok=true  target=ServiceManager.sCache key=activity expected=proxy actual=ActivityManagerProxy
hookcheck name=classLoader  ok=true  target=LoadedApk.mClassLoader pkg=mark.via expected=guest actual=PathClassLoader same=false
hookcheck name=systemProp   ok=true  target=ro.debuggable    expected=0 actual=0
hookcheck name=procSpoof    ok=true  target=/proc/self/cmdline expected=mark.via actual=mark.via len=9
hookcheck name=guard        ok=true  target=Looper.loop       expected=guarded actual=guarded intercepts=0
```

### 2.4 每类 hook 的**必带上下文**（最小集合）

| hook | 必带 context | 为什么 |
| --- | --- | --- |
| native GOT | `libs=` 被 patch 的库数、`rules=` 规则数 | `libs=0` 说明 patch 没作用到任何库 |
| Java RegisterNatives | `symbols=` 找到并替换的函数数、是否找到原符号 | 找不到原符号 = 无法零偏差，必须显式说明 |
| 系统服务代理 | `service=` 服务名、`count=` 装了几个、`actual=` 代理类名 | 装错服务/装成 host 代理都看不出 |
| Binder `sCache` | `key=`、替换前后类名 | 同服务可能被多处替换 |
| ClassLoader | `pkg=`、`same=` 是否同一实例（true 说明**没换成功**） | 最容易假成功的一项 |
| 系统属性 | `key=`、写入值、读回值 | 写入成功但读不到 = hook 没生效 |
| `/proc` | 路径、读回内容、长度 | 伪造内容可能被截断（comm 15 字符） |
| 守卫 | 是否激活、拦截次数 | `install()` 成功但没挂到 `Looper.loop` |

### 2.5 为什么成功也必须带上下文（关键论点）

| 只有 `ok=true` | 带 target/expected/actual/context |
| --- | --- |
| 知道"调用没抛异常" | 知道**挂到哪、影响多少对象、期望与实际是否一致** |
| **挂错对象也报 ok**（如挂到 host 的 `LoadedApk`） | `target` + `same=` 能立刻暴露 |
| 规则 0 条也算成功 | `rules=0` 一眼可见 |
| 无法统计"最常见失败原因" | `reason` 可聚合 |

**本项目的真实教训**：v143 的"假 dataDir" 就是这样假成功的 ——
日志显示 patch 成功，但实际打在了宿主 `ContextImpl` 的 `LoadedApk` 上，
对 MIME 毫无影响（见 `记录-假dataDir假设被否证.md`）。

---

## 三、设计：分层测试策略

| 层 | 测什么 | 手段 | 覆盖 |
| --- | --- | --- | --- |
| **① 纯 JVM** | `plan()` 的决策 + `HookCheck` 判定逻辑 | 单测 + golden | **全部 12 种**的决策部分 |
| **② Robolectric** | Java 层 hook 的真实效果 | `ShadowContextImpl` / 真实 `LoadedApk` | 服务代理、Binder sCache、ClassLoader、系统属性（Java 侧） |
| **③ 真机自检** | native / 跨进程 / 真实生效 | `debug-trace.log` 的 `hookcheck` 行 | native GOT、Java RegisterNatives、/proc |

### ① 纯 JVM 的示例（设计，不落地）

```kotlin
// 被测：决策函数（纯逻辑，无 Android 依赖）
fun planServiceProxy(serviceName: String, guestPkg: String): ProxyPlan?   // null = 不代理

@Test fun `非白名单服务不产生 plan`() { assertNull(planServiceProxy("wifi", "mark.via")) }
@Test fun `activity 服务产生代理 plan 且 target 指向 sCache`() { ... }
```

### ② Robolectric 的示例（设计，不落地）

```kotlin
@Test fun `服务代理装到 sCache 后实例类型改变`() {
    val before = ServiceManager.sCache["activity"]
    manager.installAllProxies(ctx)
    val after = ServiceManager.sCache["activity"]
    assertNotSame(before, after)
    assertTrue(after is ActivityManagerProxy)     // ← 这才是"真的挂上了"
}
```

**注意**（来自 `调研-androidx-test-core风险.md`）：Robolectric 里**不要**触发
`ActivityThreadHook`，否则容器 hook 与 Shadow 交叉。

### ③ 真机：只看 `hookcheck` 行

```powershell
Select-String trace.txt -Pattern '^hookcheck ' | ForEach-Object { ... }
# 汇总：hookcheck-summary ok=6 fail=1
```

---

## 四、实施阶段（设计，按顺序）

| 阶段 | 内容 | 产出 | 风险 |
| --- | --- | --- | --- |
| **1** | `HookCheck` + `HookCheckRegistry` + 统一输出 | 基础设施 | 低（纯新增） |
| **2** | 12 个 hook 各注册一个 check（含 2.4 的必带上下文） | 真机可归因 | 低（各 1–3 行） |
| **3** | 汇总行 `hookcheck-summary ok=N fail=M` | 回归一眼看 | 低 |
| **4** | 纯 JVM 测 `HookCheck` 判定 + `plan()` 决策 | 自动化覆盖 | 中（需抽接缝） |
| **5** | Robolectric 测 4 类 Java hook 的真实效果 | 自动化覆盖 | 中 |
| **6** | 探针 `H` 系列（H1 native / H2 javaIO / H3 服务代理 / H4 classLoader …） | 结构化对比 | 中 |

**阶段 1–3 是"让真机可归因"，阶段 4–5 是"让 hook 能自动化"，阶段 6 是"让回归可比"。**

---

## 五、对现有代码的影响评估（不改，只说明）

| 改动点 | 影响 |
| --- | --- |
| `install()` 拆成 `plan()` + `apply()` | **结构性改动**，按规则 10 必须做等价性检查；有回归风险 |
| 加 `HookCheck` 输出 | 纯新增，无行为变化 |
| Robolectric 测试 | 只加 `src/test`，不影响产物 |

**建议顺序**：先做阶段 1–3（纯新增、无风险），拿真机数据看"哪级 hook 假成功"，
再决定是否为阶段 4–5 动 `install()` 的结构。

---

## 六、与现有测试体系的关系

| 现有 | 覆盖 | `HookCheck` 补充什么 |
| --- | --- | --- |
| 纯 JVM 单测（983 行里 ~735） | 规则表、伪造内容、异常分类 | 补 `plan()` 决策层 |
| golden（2 套） | 描述文本、路径矩阵 | 补 `HookCheck` 的判定输出 |
| `module-test` | 编译 + 单测 | 跑新增的 `plan()` 测试 |
| 真机探针 W 系列 | 端到端行为 | 补 `H` 系列（hook 级） |
| **`debug-trace.log`** | 散落的事件日志 | **补结构化 `hookcheck` 行** ← 本次设计核心 |
