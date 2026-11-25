# P1-1：`:core:diagnostics` + `SelfCheckRunner`

> 原 §四


### 4.1 模块结构

```
core/diagnostics/
├── build.gradle.kts
└── src/main/java/com/nextvm/core/diagnostics/
    ├── DiagInstaller.kt       ← 注入 Diagnostics.sink
    ├── BreadcrumbTap.kt       ← 把 GuestBreadcrumb 的输出镜像进 sink
    └── SelfCheckRunner.kt     ← 6 项启动自检
app/src/debug/java/com/nextvm/app/DebugInit.kt   ← 只 debug 构建
```

### 4.2 Gradle

```kotlin
/ core/diagnostics/build.gradle.kts
dependencies {
    api(project(":core:virtualization"))
    api(project(":core:sandbox"))
}

/ app/build.gradle.kts
dependencies {
    debugImplementation(project(":core:diagnostics"))   / release 不含
}
```

### 4.2.1 执行时机：**由 CmdReceiver 命令触发，不是启动时自动跑**

`ash
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.SELFCHECK
adb logcat -d -s CmdReceiver | Select-String "SELFCHECK"
`

* 新增 CmdReceiver 的 SELFCHECK 命令 -> 同步调用 SelfCheckRunner.runNow()
* 结果写 iles/probe-out/selfcheck.json（P0-2 的目录）-> 用 CAT_OUT 导出
* **不要**用 Handler.postDelayed(5000) 启动时自动跑：那样无法区分「用户主动测试」和「日常启动」，
  而且真机上启动时跑会污染 guest 的启动耗时基线

### 4.3 六项自检

```kotlin
Check("sandbox.exists")      / files/virtual/data 可写
Check("sandbox.pathShape")   / 确认实际根是 files/virtual 而非 app_virtual
Check("hook.installed")      / NativeHookBridge 的 redirect 规则数 > 0
Check("guest.fork")          / 能对一个已装 guest 拿到 instanceId + slot
Check("webview.dataDir")     / WebViewWarmup 修好的 LoadedApk.dataDir 非空
Check("hostread.policy")     / HostReadPolicy 对 3 条典型路径的判断符合预期
```

**输出**：`files/probe-out/selfcheck.json`（P0-2 的目录），格式：

```json
{ "at": "2026-11-23T17:00:00", "results": [
  { "name": "sandbox.exists", "pass": true, "detail": "…" },
  ...
], "pass": 6, "fail": 0 }
```

### 4.4 为什么这是"用容器自己的调试能力"

它**直接调真实引擎的 API**（`SandboxManager` / `NativeHookBridge` / `WebViewWarmup`），
断言"容器自己认为它做对了什么"，而不是从外部 logcat 反推。

### 4.5 隔离保证

| 检查项 | 方法 |
| --- | --- |
| release 包不含 | `debugImplementation` + 独立模块 |
| 失败不干扰 guest | 每项 `runCatching`，异常记为 `pass=false` |
| 可关闭 | `BuildConfig.DEBUG` + SharedPreferences 开关 |

---