# 变更：采 legacy bind 路径的 golden（用 DebugFlags 开关强制切换）

- 日期：2026-11-26
- 目的：**补上启动链最后一个没有样本的分支** —— `bindApplication` 的 legacy 路径
  （现有 4 份 golden 全走 `framework makeApplication`，`docs/现状/现状-启动链测试覆盖与可测试性.md` §五 缺口 1）
- 手段：用项目既有的 **debug 开关源 `DebugFlags`**（`setprop debug.nextvm.<flag>` 可运行时切换），
  不新增机制、不加 release 负担（`DebugFlags.ENABLED = BuildConfig.DEBUG` 编译期总闸）
- 语言边界：只动 Java/Kotlin，C++ 不动

---

## 一、为什么需要一个开关（而不是"找个 guest 自然走 legacy"）

`VirtualEngine.ensureGuestApplicationBooted` 里 `frameworkLoadedApk = frameworkLoadedApks[instanceId]`
（`VirtualEngine.kt:1873`）。这个 map 在 `createFrameworkLoadedApk` 成功时才有值，而它几乎总会成功
（`ActivityThread.getPackageInfoNoCheck`）——**没有任何 guest 会自然走 legacy**，除非框架调用失败。

所以要让 legacy 分支可复现，只能显式切换。**切换的是"bind 路径"而不是 classloader**：
`createFrameworkClassLoader` 仍照常建 framework classloader（保证 guest 可启动），
只让 VAM 收到 `frameworkLoadedApk = null` → 走 `createApplication` + 手工 `attach` 的 legacy 流程。
这样"变量隔离"最干净：classloader 不变，只换 Application 的装配方式。

## 二、影响范围

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `core/common/.../DebugFlags.kt` | 修改 | 新增 `FRAMEWORK_BIND`（默认 true）+ `snapshot()` 一项 |
| `core/virtualization/.../engine/VirtualEngine.kt` | 修改 | `bindApplication(frameworkLoadedApk = …)` 按开关决定传值或 null |
| `tools/device/capture-breadcrumb-golden.ps1` | 修改 | 新增 `-LegacyBind`：采集前 `setprop … 0`，结束复位 `1` |
| `tools/gen/gen-breadcrumb-golden.mjs` | 修改 | 新增 legacy 样本组（2 次，仍要满足 C11 逐行一致） |
| `core/virtualization/src/test/.../BreadcrumbGoldenContractTest.kt` | 修改 | `Sample` 增加 `path`（framework / legacy）；C4/C7 按路径分支；新增 **C12 legacy 路径契约** |
| `docs/架构/架构-启动链契约.md` | 修改 | 记录 C12 与"两条路径各自的样本" |

## 三、逐文件改动预览

### 3.1 `DebugFlags.kt`

```diff
     /** WebView dataDir 假值（白名单 guest，默认 true）。 */
     const val WEBVIEW_DATA_DIR_FAKE = "webviewDataDirFake"
+
+    /**
+     * guest 启动是否走 framework LoadedApk bind（默认 true）。
+     * 置 0 强制走 legacy bind —— 只为采集 legacy 路径的启动链样本。
+     */
+    const val FRAMEWORK_BIND = "frameworkBind"
```

```diff
         GUEST_REDIRECT to true,
-        WEBVIEW_DATA_DIR_FAKE to true
+        WEBVIEW_DATA_DIR_FAKE to true,
+        FRAMEWORK_BIND to true
     ).joinToString(", ") { (n, d) -> n + "=" + isOn(n, d) }
```

### 3.2 `VirtualEngine.kt`（1873 行）

```diff
-                    frameworkLoadedApk = frameworkLoadedApks[instanceId]
+                    frameworkLoadedApk = if (
+                        DebugFlags.isOn(DebugFlags.FRAMEWORK_BIND, true)
+                    ) {
+                        frameworkLoadedApks[instanceId]
+                    } else {
+                        // 采 legacy 路径样本用：classloader 仍由 framework 建，只让 VAM 手工装配。
+                        // 不额外打点：路径事实由 bindApplication 的 `frameworkLoadedApk=` 表达，
+                        // 开关取值由 DebugFlags.snapshot() 落到 debug-trace.log。
+                        null
+                    }
```

### 3.2.1 为什么不加新埋点（事件输出模块复用自查）

项目已有三个事件输出模块，**本批一个都不新增、只复用**：

| 模块 | 通道 | 本批怎么用 |
| --- | --- | --- |
| `DebugFlags`（开关统一源） | `setprop debug.nextvm.*` | 新增 `FRAMEWORK_BIND`，并加进 `snapshot()` —— 复用既有 `isOn`/`snapshot` |
| `DebugTrace`（关键事件/失败） | `debug-trace.log` | **不加新事件**：`DebugFlags.init()` 已在 `bindApplication` 入口调用，`snapshot()` 里就有 `frameworkBind=<值>` |
| `GuestBreadcrumb`（有序生命周期） | `breadcrumb-<pid>.log` | **不加新事件**：`bind start instance=… frameworkLoadedApk=false` 已表达路径 |

**自查时删掉的两条**（初版写过，属重复记录同一事实）：

| 删掉的 | 已有的等价事实 |
| --- | --- |
| `DebugTrace.event("flags", "frameworkBind=false -> legacy bind")` | `DebugFlags.snapshot()`（`flags` 事件，init 时落盘） |
| `GuestBreadcrumb.log("frameworkBind=false (flag) — legacy bind")` | `bindApplication` 的 `bind start … frameworkLoadedApk=` 字段 |

**怎么区分"开关切换"与"框架失败"**（都不需要新埋点）：
`frameworkBind=false` + `frameworkLoadedApk=false` + **仍出现** `createFrameworkLoadedApk call` → 开关切换；
`frameworkBind=true` + `frameworkLoadedApk=false` + `createFrameworkLoadedApk result` 缺失/报错 → 框架失败。

### 3.3 采集脚本 `-LegacyBind`

```powershell
if ($LegacyBind) { Sh "setprop debug.nextvm.frameworkBind 0" } else { Sh "setprop debug.nextvm.frameworkBind ''" }
# …采集…
# 收尾（finally）：复位成默认（删 property）
Sh "setprop debug.nextvm.frameworkBind ''"
```

> 复位必须在 `finally`：否则下一次 framework 样本会被静默采成 legacy。

### 3.4 契约测试：按路径分支

| 契约 | framework 样本 | legacy 样本 |
| --- | --- | --- |
| C4 `makeApplication` | enter → `instr newApplication` → exit | legacy 路径**没有** `framework makeApplication`；改为断言 `legacy attachBaseContext enter/exit` 包裹 `instr newApplication` |
| C7 bind 收尾 | `bind complete (framework path)` | `bind complete (legacy path)` |
| **C12（新）** | 不出现 `legacy attachBaseContext` | 出现 `legacy attachBaseContext enter` → `legacy attachBaseContext exit` → `Application.onCreate enter`，且**不出现** `framework makeApplication` |

C1/C2/C3/C5/C6/C9/C10/C11 对两条路径同样适用（它们断的是"provider 顺序、规则注册、豁免分支"这类共性）。

## 四、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 开关泄漏到 release | `DebugFlags.isOn` 第一行 `if (!ENABLED) return false`（`ENABLED = BuildConfig.DEBUG`），R8 折叠后 release 无此代码 |
| 2 | property 未复位 → 后续样本静默变 legacy | 采集脚本 `finally` 复位；生成脚本校验每组"两次逐行一致"，跨组不一致会直接报错 |
| 3 | legacy 路径本身有已知缺陷（它正是被 framework 路径取代的旧流程） | 本批**只采样本、不改 legacy 行为**；样本固定"当前 legacy 行为"，将来若修 legacy 需走变更流程更新 golden |
| 4 | 干净安装会清沙箱数据与 appops | 按交接要点：装后重授 `MANAGE_EXTERNAL_STORAGE`，重新 INSTALL `via.apk` / `probe-new.apk` |

## 五、回退方案

```powershell
git -C _ref/ShitJumpingVM revert <本批 commit>     # 开关与 engine 改动一起回退
```
（golden 资源与测试改动一并回退；legacy 样本文件删除即可）

## 六、验证方法

```powershell
# 1) 编译容器（一次一个）
node tools/release/gh-api.mjs --dispatch container-build.yml main ref="baseline-v150"
# 2) 装机后采集（自动 setprop 0 + 复位）
.\tools\device\capture-breadcrumb-golden.ps1 -Guest mark.via -Tag via-legacy-1 -LegacyBind
.\tools\device\capture-breadcrumb-golden.ps1 -Guest mark.via -Tag via-legacy-2 -LegacyBind
# 3) 归一化 + 守卫（两次逐行一致）
node tools/gen/gen-breadcrumb-golden.mjs
# 4) 本地检查 + 容器单测
node tools/lint/kotlin-lint.mjs _ref/ShitJumpingVM/core/virtualization/src
node tools/release/gh-api.mjs --dispatch module-test.yml main ref="baseline-v150" `
  modules=":core:virtualization" test_filter="com.nextvm.core.virtualization.lifecycle.*"
```

**判据**：legacy 样本出现 `bind complete (legacy path)` + `legacy attachBaseContext`，两次逐行一致；
契约 C12 绿；framework 的 4 份旧样本行为不变（C1–C11 仍绿）。
