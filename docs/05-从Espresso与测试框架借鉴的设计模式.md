# 从 Espresso / androidx.test / Robolectric 借鉴什么（以及不借鉴什么）

- 调查日期：2026-11-21
- 已归档原文：`_ref/aosp/raw/{espresso-intents,espresso-idling,androidx-test-core,robolectric-shadow}.txt`
- 结论先行：**Espresso 本身没有"文件系统 mock"能力**；它能借鉴的是三种**结构**（声明式拦截、idle 同步、拦截窗口生命周期）。真正与"mock 文件系统"同构的是 Robolectric 的 Shadow，但它在真机上不可搬。

---

## 1. 先纠正一个前提：Espresso 到底 mock 了什么

Espresso 的核心是 UI 同步与交互（`onView(...).perform(...)`），**它不是 mock 框架**。所谓"mock 能力"实际落在 **Espresso-Intents** 这个扩展库上，而它 mock 的对象是 **Intent**，不是文件。

官方原文（`espresso-intents.txt`）：

> *"Espresso-Intents offers the `intended()` and `intending()` methods for intent validation and **stubbing**, respectively. Both take a Hamcrest `Matcher<Intent>` object."*
> *"Espresso-Intents **records all intents** that attempt to launch activities from the application under test. Using the `intended()` method, which is similar to `Mockito.verify()`, you can assert that a given intent has been seen. However, **Espresso-Intents doesn't stub out responses to intents unless you explicitly configure it**."*

拆解出三个可直接借鉴的设计：

| Espresso-Intents 的构件 | 作用 | 我们的对应物 |
| --- | --- | --- |
| `Matcher<Intent>` 规则 | 声明"命中什么" | 组件拦截的匹配条件 |
| `intending(matcher).respondWith(...)` | 命中后给桩响应 | 权限请求 → 代答；站内 Intent → StageA |
| `intended(matcher)` 记录与断言 | 事后校验"发生过什么" | 诊断日志：guest 到底发了哪些 Intent |
| `Intents.init()` / `Intents.release()` | 拦截**只在窗口内**生效 | Host/Target 调用窗口 |
| 默认**只记录不拦截** | 保守默认 | 我们应默认"放行 + 诊断"，只对白名单规则拦截 |

**它的实现底层恰好就是我们在做的事**：Espresso-Intents 通过注册 `IntentMonitor` 挂在 `Instrumentation.execStartActivity` 这个钩子上——与 NestSpace `ContainerInstrumentation.execStartActivity` 是同一个位置。
差别不在钩子，而在**抽象层次**：他们是"匹配器 + 规则表 + 记录器"，我们是 if-else 硬编码。

---

## 2. 可借鉴点 1：把组件拦截改成「规则表 + 匹配器 + 桩响应」

当前写法（NestSpace `ContainerInstrumentation.execStartActivity`，简化）：

```kotlin
if (handlePermissionRequest(session, host, target, intent, requestCode)) return null
if (session.startTargetActivity(host, intent, requestCode)) return null
if (session.startExternalActivity(host, intent, requestCode)) return null
```

问题：规则越多，这个函数越不可维护、越难测试、越难按 App 灰度。

借鉴 Espresso 的结构改成声明式：

```kotlin
data class IntentRule(
    val name: String,                    // 便于诊断与灰度
    val match: (Intent, Activity?) -> Boolean,
    val action: Action,                  // REDIRECT_TO_STAGE / STUB_GRANT / PASS_HOST / DENY
    val enabled: Boolean = true
)

// 注册表
val rules = listOf(
    IntentRule("permission-controller", ::isPermissionController, STUB_GRANT),
    IntentRule("in-package", { i, _ -> i.component?.packageName == session.pkg }, REDIRECT_TO_STAGE),
    IntentRule("browser", { i, _ -> i.action == ACTION_VIEW && i.scheme in httpSchemes }, REDIRECT_TO_INTERNAL_WEBVIEW),
    IntentRule("external", { _, _ -> true }, PASS_HOST)
)
```

收益（都是本项目真实痛点）：

1. **可按 App 灰度**：规则带 `enabled` 与 profile 绑定（NestSpace v1.0.7 的 runtime profile 就是干这个的）；
2. **可测试**：规则是纯函数，可以脱离真机跑单测；
3. **可诊断**：每条规则命中都记一条 breadcrumb——**这正是"guest 到底想干什么"的最高价值日志**（比读 `/proc` 有用得多）；
4. **保守默认**：兜底规则 = `PASS_HOST`，与 Espresso"默认只记录"一致，避免误拦截。

> 落地建议：先只把"权限请求 + 站内 Activity"两条改成规则，其余保持原状，再逐步迁移。不要一次性重写。

---

## 3. 可借鉴点 2：IdlingResource 式同步，替代"固定 sleep"

官方原文（`espresso-idling.txt`）：

> *"By registering idling resources with long-running operations, you must register each one as an idling resource."*
> *"`CountingIdlingResource` ... `UriIdlingResource` ..."*

Espresso 解决的是"**如何在异步任务完成前不误判**"。而我们的容器有**完全相同的问题**：

| 容器里的"长任务" | 现在的做法 | Idling 化后的做法 |
| --- | --- | --- |
| guest `Application.onCreate()` | 回调里推进阶段 | `CountingIdlingResource("app.onCreate")` 计数 +1/-1 |
| Provider 初始化 | 顺序执行 | 同上 |
| 首个 Activity 首帧 | 阶段事件 | 同上 |
| WebView 初始化 | 缓存 UA 猜测 | 同上 |
| 首次网络请求 | 无 | 同上 |

**最有价值的场景是我们自己的 AI/MCP 自动化**：AI 点一下之后要等页面稳定，靠固定 `sleep` 必然不稳（NestSpace 已经删掉了 2.5 秒固定 settle，但换成了"阶段事件"，仍缺一个统一的 idle 抽象）。

建议做一个极简实现：

```kotlin
object ContainerIdling {
    private val counters = mutableMapOf<String, AtomicInteger>()
    fun begin(tag: String) / fun end(tag: String)
    fun isIdle(): Boolean = counters.values.all { it.get() == 0 }
    // 自动化调用前：await(isIdle(), timeoutMs) + 主线程 idle（post 空消息验证队列已空）
}
```

配套的"主线程 idle 检测"用 `Handler.post { }` 空消息判定 looper 队列已排空即可——这就是 Espresso `loopMainThreadUntilIdle` 的最小等价物。

---

## 4. 可借鉴点 3：Rule 生命周期 = 拦截窗口

`Intents.init()` / `release()` 的语义是"**拦截只在显式开启的窗口内发生**"。

这正好解决我们的"宿主自己不能被改写"问题：

- native 侧：我们用"**只 patch 目标模块的 GOT/PLT**"已经天然满足（见 `03` 文档 4.3）；
- Java 侧：仍然需要一个窗口——因为 `mInstrumentation` 被替换后，宿主自己的 Activity 也会经过它（NestSpace 用"判断是不是 StageA"来分流）。
- 借鉴做法：给代理层加显式的 `enterTargetWindow()` / `exitTargetWindow()`（可嵌套、异常安全），窗口外一律走真实实现。

---

## 5. Robolectric 的 Shadow：思路可借，机制不可搬

官方原文（`robolectric-shadow.txt`）：

> *"Robolectric fills these gaps with a set of classes known as **Shadows**. Each shadow can modify or extend the behavior of a corresponding class in the Android OS."*
> *"Using **byte code instrumentation**, Robolectric is able to weave in cross-platform fake implementations."*
> *"Shadow objects are not quite Proxies, not quite Fakes, not quite Mocks or Stubs."*

**关键限制**：它靠 **JVM 字节码织入**在测试运行时替换 framework 实现。真机上 `android.*` 来自 boot classpath，ART 已加载，**无法用同样方式替换**。

| 做法 | 真机可行性 |
| --- | --- |
| Robolectric Shadow（字节码织入） | ❌ 仅 JVM 单测 |
| Mockito `mockito-inline`（mock final 类，如 `java.io.File`） | ❌ 需字节码改写/ART hook，生产不可用 |
| 动态代理替换系统服务（`IActivityManager` 等接口） | ✅（我们已在做） |
| 用子类/Wrapper 替换（Context、ContextWrapper） | ✅（我们已在做） |

**可借的一点**：Robolectric 的"**类 → 影子类**"关联表，与我们的"**真实服务 → 虚拟服务**"注册表是同构的。把这层注册表显式化（而不是散在各处 `if`），能让"哪些系统服务被虚拟化"一目了然，也便于按 App 灰度。

---

## 6. 最有价值的一条：`TemporaryFolder` 的"根 + 相对"路径模型

测试框架普遍给被测代码一个"**临时根目录**"，被测代码只用"根 + 相对路径"。这个模型可以直接搬进我们的 Context 层：

```kotlin
// 所有虚拟路径都由一个 root 生成
class SandboxRoot(val root: String) {
    fun files() = "$root/files"
    fun cache() = "$root/cache"
    fun db(name: String) = "$root/databases/$name"
}
```

于是**方案 A 与方案 B 的差别退化成"换一个 root 字符串"**：

| 方案 | root 取值 | 是否需要 native 映射 |
| --- | --- | --- |
| A（真路径） | `/data/user/0/<host>/files/vs/data/<guest>/<inst>` | 不需要 |
| B1（虚拟标准路径） | `/data/user/0/<guest>` | 需要（libc 前缀表） |
| B2（半虚） | `/data/user/0/<host>/vs/<guest>` | 不需要 |

**这是一个非常实用的具体建议**：从第一天起就把 Context 层实现成"root + 相对路径"，**不要**在十几处硬编码沙箱路径（NEXTVM 的 `VirtualContext` 就是每处直接调 `sandboxManager.getXxxDir()`，换方案要改十几处）。

这样"方案 A → B1"的升级只是：换 root 字符串 + 打开 native 前缀表，**不动 Context 与业务代码**。

---

## 7. 明确不借鉴的三件事

1. **不引入 Espresso / androidx.test 作为运行时依赖**：它们要求 instrumentation 测试进程 + `AndroidJUnitRunner`，不能给容器内的 guest 用；只借鉴 API 形态。
2. **不做 JVM 上的"容器"**（Robolectric 路线）：我们的目标是真机运行第三方 APK，JVM 模拟没有意义（而且 Robolectric 也不支持大多数 native 依赖）。
3. **不用 Mockito 在生产环境 mock framework**：会污染全局状态，且需要字节码改写。

---

## 8. 一句话结论

> Espresso 的"mock"是 **Intent 桩化 + 记录**，不是文件系统 mock。但它给了我们三个**成本低、收益高**的结构：
> ① **规则表 + 匹配器 + 桩响应**（替换 if-else 拦截链，可按 App 灰度并可诊断）；
> ② **IdlingResource 式计数器**（替代固定 sleep，直接提升 AI/MCP 自动化的稳定性）；
> ③ **init/release 式拦截窗口**（保证宿主自身不被改写）。
>
> 而"mock 文件系统"真正可借的是测试框架的 **"根 + 相对路径"模型**：把 Context 层做成"一个 root 生成所有路径"，方案 A/B 的切换就退化为换一个字符串——这是本系列里**投入产出比最高的一条设计决定**。
