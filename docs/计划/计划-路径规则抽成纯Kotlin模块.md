# 计划：把路径规则抽成纯 Kotlin 模块（**暂不执行**）

- 日期：2026-11-25
- 状态：**计划，暂不执行**（用户：记录计划，现在先改测试和容器）
- 关联：`docs/输出/调研/调研-改而不取代的机制.md`（方案 B1）

---

## 一、要解决的问题：测试拖上了整个模块

`module-test` 跑 `:core:hook:testDebugUnitTest` 时，实际执行的 task：

```
kspDebugKotlin                     ← Hilt KSP 注解处理
compileDebugKotlin                 ← 7000+ 行 NativeHookBridge + 全部其他源文件
compileDebugJavaWithJavac
transformDebugClassesWithAsm       ← Hilt ASM 字节码变换
bundleLibRuntimeToJarDebug
bundleLibCompileToJarDebug
kspDebugUnitTestKotlin
compileDebugUnitTestKotlin
testDebugUnitTest                  ← 真正的测试只有这一步
```

而 `NativeHookBridgePathTest` 只用到 `translatePath` / `addPathRedirection` /
`setupAppRedirections` / `setupExternalStorageRedirections` —— **约 150 行纯函数**。

**CMake 没跑**（这点还好），但 Kotlin 全模块 + Hilt 全跑。实测一趟 44s。

---

## 二、目标形态

```
:core:pathmap                          ← 纯 Kotlin（`java-library` 或 `kotlin("jvm")`，无 Android）
  src/main/kotlin/.../PathRedirectRegistry.kt
      addPathRedirection(from, to)
      translatePath(path): String
      hidePath / isPathHidden
  src/test/kotlin/.../PathRedirectRegistryTest.kt
      ← 现有 38 条用例迁过来

:core:hook                             ← 依赖 :core:pathmap
  NativeHookBridge 内部持有 registry，@Deprecated 转发到新类
```

**收益**：

| 项 | 现在 | 抽出来后 |
| --- | --- | --- |
| 编译范围 | `:core:hook` 全模块（7000 行 + Hilt/KSP） | 一个 ~200 行纯 Kt 模块 |
| 跑的 task | KSP + compile + ASM + bundle + test | `compileKotlin` + `test` |
| 实测耗时 | 44s | 预期 <10s |
| 依赖 | Android SDK + NDK + Hilt | 只要 JDK + JUnit |

**顺带的一致性收益**：`main` 分支其实**已经做过这个拆分**（有 `PathRedirectRegistry` 类），
只是它的测试期望值钉住了错误行为（`external_data`/`obb`）。**方向对，断言错。**

---

## 三、执行前必须先解决的三个问题

| # | 问题 | 说明 |
| --- | --- | --- |
| 1 | **`NativeHookBridge` 的 7000 行里有多少处用到 registry** | 需要先统计调用点，避免拆分时漏改 |
| 2 | **`main` 分支的 `PathRedirectRegistry` 能否直接借用** | 若能用，等于现成的实现；但它的测试要按正确行为重写 |
| 3 | **规则镜像文档**（规则 7） | 新模块的每个文件都要 `docs/代码镜像/core/pathmap/*.md` |

**还要遵守规则 10**（拆分必须证明逻辑未变）：用 `check-refactor-equivalence.mjs`
对照 `translatePath` 的旧实现与新实现。

---

## 四、不做什么

| ❌ | 原因 |
| --- | --- |
| 不加新功能 | 纯搬运，行为必须逐字一致 |
| 不改 `translatePath` 的算法 | 本轮刚修完 `String.replace` → `substring` 的 bug，先稳定 |
| 不同时做方案 B1（动态代理 `File.fs`） | 两件事叠加风险高 |
