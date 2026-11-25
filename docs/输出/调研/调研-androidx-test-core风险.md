# 评估：`androidx.test:core` 用于调试测试的可行性、环境变化与容器风险

- 日期：2026-11-24
- 触发：用户允许在遇到困难时使用 `androidx.test:core`，但要求先调查环境变化与对容器的危害
- 结论：**可以用，且对容器运行时零危害**（test-only 不进 APK）；
  但**不建议用 `ActivityScenario`** —— 会真实创建 Activity，与容器的全局 hook 冲突。

---

## 一、它是什么

| 组件 | 作用 |
| --- | --- |
| `androidx.test:core` | 提供 `ApplicationProvider`（取 Application）、`ActivityScenario`（驱动 Activity 生命周期）、`IntentTest` |
| 传递依赖 | `androidx.test:monitor`、`androidx.test:runner`、`androidx.annotation` |

## 二、对容器的危害评估

### ① 打包影响：**零**

```kotlin
testImplementation("androidx.test:core:1.x")   // test 源集
```

`testImplementation` **不进 APK** —— 产品包体积、方法数、权限清单都不变。
`./gradlew :app:assembleDebug` 的产物与不引入时**完全一致**。

### ② 运行时影响：**零**

只在 `testDebugUnitTest` 的 JVM 进程里生效，不参与容器运行。

### ③ 真正的风险：`ActivityScenario` 与容器全局 hook 冲突 ⚠️

这是**本项目特有**的风险，通用资料里不会提：

| 事实 | 后果 |
| --- | --- |
| 容器用 `ActivityThreadHook` 挂钩 `ActivityThread.mH` 的 Callback（**进程级全局**） | 一旦测试进程里跑过 hook 安装，后续所有 Activity 创建都会经过它 |
| Robolectric 的 `ActivityThread` 是 `ShadowActivityThread`，**不提供 `mH` 队列** | hook 安装可能静默失败，或挂到错误的对象上 |
| `ActivityScenario.launch()` 会真实走 `Activity` 生命周期 | 可能触发 `handleLaunchActivityItem` → 里面的反射/classloader 替换在 Shadow 环境下行为未定义 |

**判断**：

| API | 安全性 | 说明 |
| --- | --- | --- |
| `ApplicationProvider.getApplicationContext()` | ✅ **安全** | 等价于 `RuntimeEnvironment.getApplication()`，不创建 Activity |
| `RuntimeEnvironment.getApplication()` | ✅ **安全** | 当前已用这个，**不需要新增依赖** |
| `ActivityScenario` / `ActivityScenarioRule` | ⚠️ **有风险** | 会触发 Activity 生命周期 → 与容器 hook 交叉 |
| `IntentTest` / `Espresso` | ⚠️ 视情况 | Espresso 不适用于本项目的测试目标 |

## 三、环境变化清单

| 变化 | 影响 |
| --- | --- |
| 新增 test 依赖 ~3 个 artifact | 首次下载约 2–5MB；`module-test.yml` 的 Gradle 缓存已覆盖 |
| 可能引入版本冲突 | 仅当项目已用不同版本 `androidx.test` 时；当前 `libs.versions.toml` **没有任何 androidx.test 条目**，无冲突 |
| 测试执行变慢 | `ApplicationProvider` 与 `RuntimeEnvironment` 等价，无额外成本；`ActivityScenario` 会慢（每个用例重建 Activity） |

## 四、结论与使用建议

| 场景 | 建议 |
| --- | --- |
| 只想拿 Application/Context | **用 `RuntimeEnvironment.getApplication()`，不引入依赖**（当前做法） |
| 需要 `ActivityScenario` 驱动 Activity | 引入 `androidx.test:core`（test-only），但**必须先确认不触发容器 hook**：测试开始前不要调用 `ActivityThreadHook.install()`，或显式 `uninstall()` |
| 需要 `Espresso` 式 UI 交互 | 不适合本项目（容器行为在真机才能验） |

**如果将来引入**，加在 `gradle/libs.versions.toml`：

```toml
androidx-test-core = { group = "androidx.test", name = "core", version = "1.6.1" }
```

并在模块里 `testImplementation(libs.androidx.test.core)` —— 依旧 test-only。

## 五、当前选择（不改）

`VirtualContextPathGoldenTest` 用 `RuntimeEnvironment.getApplication()`，
**没有引入任何新依赖**，因为该测试只需要 Context 骨架，不需要 Activity 生命周期。

**只有**出现"必须驱动 Activity 生命周期"的需求时，才按第四节引入并遵守那条约束。
