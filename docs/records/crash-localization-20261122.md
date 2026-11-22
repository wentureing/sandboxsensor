# 崩溃定位：不是重启、不是 GPU 普遍 bug，而是「MainActivity pause 的最后一帧」

- 日期：2026-11-22 16:47–16:52
- 设备：vivo V2463A / Android 15 / Adreno（`ro.hardware.vulkan=adreno`）
- 容器：ShitJumpingVM + p1 补丁
- 原始日志：`../probe-reports/launch-164933.txt`（496 KB）

---

## 1. 对照实验（全部在 `pm clear` 后的干净状态）

| 组 | 条件 | 重启后 | 结论 |
| --- | --- | --- | --- |
| A | 无 guest | ✅ 不崩（pidof=25082, crash=0, Displayed=1） | 容器自身没问题 |
| B | 只装 YESPDF（有 icon） | ✅ 不崩（pidof=26239） | 第三方 app 不触发 |
| C | 装探针（无 application icon）**但不启动** | ✅ 不崩（pidof=26463） | 单纯"存在 guest"不触发 |
| D | 装探针 + **执行 LAUNCH** | ❌ 主进程崩（crash=1），**但 guest 进程跑完并输出完整报告（17 块）** | 触发点在"启动 guest" |
| E | D 之后立刻重启容器 | ✅ 不崩（pidof=27140） | 崩溃不污染持久化状态 |

**"装 app 后重启就崩"的直觉是错的**：真正的触发条件是 **LAUNCH guest**，而不是重启本身。重启只是让用户"看到"主界面重新渲染而已。

---

## 2. 崩溃时序（关键证据）

```
16:51:13.410  27140 27140 I wm_on_paused_called:
                          [66537782, com.nextvm.app.MainActivity, performPause, 0]   ← MainActivity 被暂停
--------- beginning of crash
16:51:13.414  27140 27327 F libc: Fatal signal 6 (SIGABRT) ... RenderThread
                                  pid 27140 (com.nextvm.app)                        ← 主进程 RenderThread
16:51:13.440  I am_proc_bound: [0, 27250, com.nextvm.app:p1]                         ← 同时拉起 guest 进程
```

崩溃栈（每次完全一致）：

```
#00  libc.so              abort+172
#01-#05  libllvm-glnext.so         ← Adreno shader 编译器
#06-#09  vulkan.adreno.so          vkCreateGraphicsPipelines
#10-#16  libhwui.so                GrVkPipeline::Make
#17-#19  libhwui.so                FillRectOpImpl::onExecute   ← 只在画一个填充矩形
#20-#29  libhwui.so                CanvasContext::draw → RenderThread::threadLoop
```

**因此崩溃 = 启动 guest 时 MainActivity 进入 paused，触发最后一帧重绘，某个基础 pipeline 变体在 Adreno 编译器里 abort。**

与 guest 是什么 app 无关（探针、YESPDF 都可能，只要触发 LAUNCH 就走同一条路径）。

---

## 3. 为什么之前"必须清数据才能恢复"

多次失败（p1 之前每次 LAUNCH 都因 `Resources$NotFoundException` 崩 Activity）会在 DataStore 里留下半初始化实例。
主进程在写 DataStore 途中被 SIGABRT 杀掉 → 状态损坏 → 下次启动读取损坏状态时更早崩。

`pm clear` 后：
- 组 D 证明：**即使主进程崩了，重启也能恢复**（因为 p1 之后 guest 不再因 Resources 崩，没有产生严重污染）
- 所以 p1 补丁顺带缓解了"必须清数据"的问题

---

## 4. 已试过的绕过

| 方案 | 结果 |
| --- | --- |
| `setprop debug.hwui.renderer skiagl` | ❌ 无效：栈里仍是 `vulkan.adreno.so`，说明属性未生效或 Vulkan 仍被使用 |
| 清容器数据 | ✅ 临时恢复，但不解决 LAUNCH 触发 |

**尚未尝试**：

1. `setprop debug.hwui.renderer opengl`（老 GL 后端，值需确认）
2. 容器 manifest 给 `MainActivity` 加 `android:hardwareAccelerated="false"`（软件渲染，UI 慢但不走 Vulkan）
3. 代码里对 MainActivity 的 Window 设 `setLayerType(LAYER_TYPE_SOFTWARE, null)`
4. 换设备（非 Adreno 或其它驱动版本）

---

## 5. 三个隐藏/虚拟化缺陷的代价评估

| 缺陷 | 现状 | 修复位置 | 代价 | 隐藏代价（提升后要付的） |
| --- | --- | --- | --- | --- |
| **`ApplicationInfo.uid = 0`** | T2 报告 `J19` 显示 `uid=0` | 构造 guest `ApplicationInfo` 时赋值 | **极低**（1 行：设为宿主 uid） | 设为宿主 uid 后 `myUid == officialUid` 保持一致（符合 `07 §8` 的 observedUid 策略）；若将来要伪造独立 uid，则必须同时改 native `getuid` + `/proc/self/status`，成本陡增 |
| **`targetSdkVersion = 0`** | T2 `J20` 显示 `targetSdk=0` | 从 guest `PackageInfo` 复制 | **极低** | 这是**隐性高风险**：targetSdk=0 会让 framework 走最老兼容模式，scoped storage / 前台服务 / Binder 限制等行为全部退化，反而制造"和真实安装不一致"的新破绽 |
| **`deviceProtectedDataDir` 泄漏宿主** | T2 `J18`：`/data/user_de/0/com.nextvm.app` | 需映射 `/data/user_de/0/<guest>` → 沙箱 | **中** | 还要处理 Direct Boot 语义（DE 存储在解锁前可访问），以及 CE/DE 两套上下文的隔离；不做则 App 在 Direct Boot 前把数据写到宿主目录 |
| **`getInstalledPackages = 1`** | T2 `J21`：只有自己 | 需要 PM 代理返回"宿主真实包 + 虚拟包"合并列表 | **高** | 走 `QUERY_ALL_PACKAGES` 会被 Play 政策限制且是强风控特征；不走则分享/推送/支付类 SDK 看不到其他应用，功能残缺。这是**收益与风险都最高**的一项 |

排序建议：先修 `uid` + `targetSdk`（各 1 行，收益立竿见影），再处理 `deviceProtectedDataDir`（中等），`getInstalledPackages` 放到最后并做成可开关。

---

## 6. 结论

1. **崩溃不是"重启"引起，而是"LAUNCH guest 时 MainActivity pause 的最后一帧"** 触发的 Adreno 驱动 abort
2. 与 guest 内容无关（探针 / YESPDF 路径相同），与"是否启动过 guest"强相关
3. `p1` 补丁修掉了 Activity 的 Resources 崩溃，顺带让"必须清数据"的问题消失
4. 三个虚拟化缺陷中，`uid` 和 `targetSdk` 是**低成本高收益**，`deviceProtectedDataDir` 中等，`getInstalledPackages` 高成本且带政策风险
