# T2-pre 记录：探针在 ShitJumpingVM（原版）容器内运行

- 日期：2026-11-22 15:06–15:09
- 设备：vivo V2463A / Android 15 / API 35 / arm64-v8a / PAGESIZE 4096
- 容器：`com.nextvm.app` v1.0.0-alpha01（`wentureing/ShitJumpingVM` fork，原版未改）
- 探针：`com.sandbox.probe` v1.0（51 项 + 6 provider）
- 数据源：`../probe-reports/logcat-dump-20261122-151219.txt`（`adb logcat -d -b all`）

---

## 1. 结论速览

| 项 | 结果 |
| --- | --- |
| 容器导入未安装 APK | ✅ 成功（沙箱 APK 466,644 bytes） |
| ClassLoader 替换 | ✅ `replaceClassLoader host=#23739684 guestCL=#110875219` |
| `LoadedApk` 重建 | ✅ `createFrameworkLoadedApk result loadedApk=#142987703` |
| guest `Application` 创建 | ✅ `makeApplication exit app=com.sandbox.probe.ProbeApplication` |
| guest `ContentProvider` 初始化 | ✅ `provider ok com.sandbox.probe.ProbeProvider` |
| guest `Application.onCreate` | ✅ `Application.onCreate exit ok` |
| **guest `Activity` 启动** | ❌ `Resources$NotFoundException` |
| 探针 4 个 PHASE 输出 | ✅ 全部出现（见 §3） |
| 完整 57 项报告 | ❌ 未产出（崩在 `new TextView(this)`） |
| 容器主进程 UI 稳定性 | ❌ RenderThread SIGABRT ×15（GPU 驱动） |

**一句话**：容器把 guest 跑到了 `Application.onCreate` 成功，**卡在 Activity 的资源绑定**；探针自身的 UI 依赖放大了这个问题。

---

## 2. 成功链路（容器 breadcrumb，pid 28945 = `:p0`）

```
[+0ms]  breadcrumb begin pkg=com.sandbox.probe pid=28945
[+0ms]  replaceClassLoader enter pkg=com.sandbox.probe instance=com.sandbox.probe_1795331202502
[+0ms]  createFrameworkLoadedApk result loadedApk=#142987703 ai.sourceDir=/data/...
[+2ms]  replaceClassLoader done mPackages[guest]=#80824457 mPackages[host]=#23739684
[+3ms]  v79 probe target=com.sandbox.probe.ProbeActivity clone=#80824457
        mResources(before)=#85879611 zipOpen=ok entries=6 getResources()=THREW   ← ★ 根因
[+6ms]  v118-provider-fields name=com.sandbox.probe.ProbeProvider initOrder=1000
[+11ms] bind start instance=... appClass=com.sandbox.probe.ProbeApplication providers=1
[+11ms] framework makeApplication enter
[+11ms] framework bind identity loadedApk=#142987703 mPackageName=com.nextvm.app
        ai.packageName=com.sandbox.probe                              ← 包名已虚拟化
[+13ms] instr newApplication com.sandbox.probe.ProbeApplication clId=110875219
[+14ms] framework makeApplication exit app=com.sandbox.probe.ProbeApplication
[+15ms] providers begin n=1 list=com.sandbox.probe.ProbeProvider
[+16ms] provider ok com.sandbox.probe.ProbeProvider
[+16ms] Application.onCreate enter / exit ok
[+17ms] v130-watchdog started
```

容器侧动作（主进程 pid 27007）：

```
VAMS    : Started activity: com.sandbox.probe.ProbeActivity → com.nextvm.app.stub.P0$Standard00 (task=1001)
VirtEnvHook: Environment hooks active ... -> /data/user/0/com.nextvm.app/app_virtual/data/com.s...
NextVM-Native: Path redirect added: /sdcard/ 、/storage/emulated/0/ 、/mnt/sdcard/ 、
                                    /storage/self/primary/ 、/storage/emulated/0 、/sdcard
ATHook  : ActivityThread.mH hook installed successfully          ← 走 EXECUTE_TRANSACTION 路线
StubRegistry: Built stub mappings: 10 slots, 10 activities/slot
ATHook  : Intercepted stub launch → swapping to com.sandbox.probe.ProbeActivity
VProcessManager: Allocated slot 0 (:p0) for com.sandbox.probe_1795331202502
VPMS    : Registered package from disk: com.sandbox.probe
VPermissionMgr: Permissions initialized for com.sandbox.probe: 16 ALL granted (auto-grant mode)
```

> 注意 native 只注册了**外部存储**前缀重定向（`/sdcard` 系列），**没有注册 `/data/data/<pkg>` 或 `/data/user/0/<pkg>`** —— 这正是「方案 A 真路径」的直接证据，也解释了为什么 T1 的 `J26/J27` 在 T2 下必然失败。

---

## 3. 探针 4 个 PHASE 全部输出（两次尝试）

```
15:06:55.393 pid=28945 I PROBE: PHASE Application.attachBaseContext uptime=2516475541
15:06:55.394 pid=28945 I PROBE: PHASE ContentProvider.onCreate      uptime=2516475542
15:06:55.395 pid=28945 I PROBE: PHASE Application.onCreate          uptime=2516475543
15:06:55.426 pid=28945 I PROBE: PHASE Activity.onCreate             uptime=2516475575
15:06:59.4xx pid=30853 I PROBE: PHASE ...（同上，容器重启 guest 后第二次尝试）
```

- **Provider 早于 Application.onCreate** 在容器内同样成立（与 T1 一致）
- `process=com.sandbox.probe` 说明 `Application.getProcessName()` 已返回 guest 名（容器做了进程名伪装）
- 探针日志**能进 logcat** → 之前判断的"vivo 过滤应用日志"是错的，实际是 `logcat -c` + 短窗口 + 未用 `-b all` 导致抓不到

---

## 4. 崩溃 1：guest Activity 资源绑定失败（阻塞 T2 的主因）

```
FATAL EXCEPTION: main
Process: com.sandbox.probe, PID: 28945
java.lang.RuntimeException: Unable to start activity
  ComponentInfo{com.sandbox.probe/com.sandbox.probe.ProbeActivity}:
  android.content.res.Resources$NotFoundException
    at android.app.ActivityThread.performLaunchActivity(ActivityThread.java:4676)
    ...
    at com.nextvm.core.virtualization.engine.NextVmInstrumentation.callActivityOnCreate(ActivityThreadHook.kt:1841)
am_crash: [28945,0,com.nextvm.app:p0,551042630,android.content.res.Resources$NotFoundException,
           Resource ID #0x7f0a0000]
```

- 根因线索：breadcrumb `getResources()=THREW`、`mResources(before)=#85879611`
- 直接触发点：探针 `ProbeActivity` 里的 `new TextView(this)` / `setContentView`
  （`Resource ID #0x7f0a0000` 是 `TextView` 构造时查 style 属性）
- **即使容器修好 Resources，探针也不该依赖 View** —— 探针的目的是测 API，不是测渲染

---

## 5. 崩溃 2：容器主进程 RenderThread（GPU 驱动，独立问题）

15 次，全部 `com.nextvm.app` / `RenderThread`：

```
#00 libc.so            abort+172
#01-#05 libllvm-glnext.so     ← vivo/Adreno shader 编译器
#06-#09 vulkan.adreno.so      vkCreateGraphicsPipelines
#10-#16 libhwui.so            GrVkPipeline::Make
#17-#19 libhwui.so            FillRectOpImpl::onExecute   ← 画一个填充矩形
#20-#29 libhwui.so            CanvasContext::draw → RenderThread::threadLoop
```

- 栈里没有 `libprobe.so` / `libnextvm-native.so` → 与 guest 无关，是容器 Compose UI 的渲染路径撞上驱动 bug
- 后果：进程写 DataStore 途中被 SIGABRT 杀掉 → 数据损坏 → 用户侧表现为"必须清数据才能恢复"
- 可能绕过：`setprop debug.hwui.renderer skiagl`（全局 debug 属性，未执行）

---

## 6. 其他观察

- SELinux 拒绝：`avc: denied { link } for comm="v81-apk-watch" name="com.sandbox.probe_1795331202502.apk"`
  → 容器的 APK 监视逻辑被 SELinux 拦，可能影响 APK 更新/校验
- 沙箱已建好 guest 数据目录：`files/virtual/data/com.sandbox.probe_.../{app_virtual,cache,code_cache,files,shared_prefs}`
- `-Xcheck:jni` 在 `:p0` 默认开启（debug 构建），会放大 JNI 误用为 abort

---

## 7. 下一步（按优先级）

1. **探针去 UI 化**：`ProbeActivity` 不创建任何 View、不 `setContentView`，直接跑 `ProbeRunner` 后 `finish()` → 绕开 Resources 问题，拿全 57 项
2. 探针在 `Provider.onCreate` 后立即落盘一份 timeline 快照 → 即使 Activity 崩也能留证据
3. 容器侧（后续）：`getResources()` 抛异常是 P0 缺陷，需对照 `05 §6` 的 root+resolver 重构
4. 主进程渲染崩溃：先试 `debug.hwui.renderer skiagl`，再决定是否改容器渲染路径
