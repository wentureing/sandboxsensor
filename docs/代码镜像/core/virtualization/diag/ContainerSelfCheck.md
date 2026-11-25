# ContainerSelfCheck（core:virtualization / diag）

- 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/diag/ContainerSelfCheck.kt`
- 测试：`core/virtualization/src/test/java/com/nextvm/core/virtualization/diag/ContainerSelfCheckTest.kt`（C1–C7）
- 触发：`CmdReceiver` 的 `com.nextvm.cmd.SELF_CHECK`
- 日期：2026-11-25

## 职责

**负责**：**主动**对容器自身做行为断言——真的注册一条临时规则、真的写一个文件、真的写一次诊断，
然后检查结果是否符合预期，输出一行 `hookcheck ok=N fail=M`。

**不负责**（边界）：

| 不负责 | 在哪 |
| --- | --- |
| 被动观察（读已有日志/文件） | 探针（`probe/`）的 D/J/F 系列、`checks.log` |
| guest 内部行为 | 探针（独立 APK，跑在 guest 进程里） |
| 真实路径族的全量断言 | `PathKindsTest`（CI，纯 JVM）+ 探针 J 系列（设备） |
| 修复任何东西 | 自检只报告，不改配置、不重建规则 |

**一句话**：自检回答"容器现在还能不能完成它承诺的动作"，不是"日志里写了什么"。

## 函数

| 函数 | 签名 | 作用 |
| --- | --- | --- |
| `run` | `(): List<Check>` | 跑 6 项检查；每项独立 try/catch，单项失败不影响其它 |
| `report` | `(List<Check>): String` | 生成 `hookcheck ok=N fail=M S1:ok ...` |
| `ruleHit`（S1） | 私有 | 加临时规则 `/selfcheck/in/ → /selfcheck/out/`，断言 `translate` 命中；finally 删除 |
| `ruleRemoved`（S2） | 私有 | 删除后断言不再命中（证明"删得掉"，避免规则泄漏） |
| `isolatedWrite`（S3） | 私有 | 临时规则指向 `sandboxRoot/selfcheck/w/`，真的写文件，断言**落在沙箱内**；finally 删文件与规则 |
| `unknownPathUntouched`（S4） | 私有 | 未注册路径必须原样返回（防止误伤） |
| `diagChannel`（S5） | 私有 | 向 `debug-trace.log` 追加一行并断言长度增长；`diagFile=null` 时 **skipped 视为通过** |
| `configSanity`（S6） | 私有 | `path_spoof.conf` 存在且含 `[豁免名单]` 段 |

## 偏差登记

| # | 偏差 | 为什么 | 类型 | 如何观测 |
| --- | --- | --- | --- | --- |
| 1 | S3 用**临时规则**+真实文件，而不是写真实 `/sdcard` | 自检不能污染真实外部存储，也不能依赖 appops/权限状态 | 必要偏差 | S3 的 `detail` 含 `target=... inSandbox=...` |
| 2 | S5 在无诊断文件时返回 `ok=true, detail=skipped(no diag file)` | release 构建不产出诊断文件；"跳过"不等于"失败" | 中性偏差 | S5 detail |
| 3 | S3 写 **`context.cacheDir/selfcheck`** 而不是 `virtualRoot` | `virtualRoot` 是用户/Files 页面可见目录，写进去会污染（实机踩过）；cacheDir 私有且可回收 | 必要偏差（2026-11-25 修正） | S3 detail 的 `target=.../cache/selfcheck/w/f.txt` |
| 4 | S6 只检查段名存在，不解析业务内容 | 自检不该复刻配置解析（那由 `PathSpoofConfig` 负责） | 中性偏差 | S6 detail `len=...` |
| 4 | 不检查 guest 侧（`getFilesDir` 等） | 容器主进程上下文拿不到 guest 的 `Context` | 必要偏差 | 由探针 J 系列覆盖 |

## 约束与坑

1. **CI 与设备的执行分界（用户 2026-11-25 决定）**：
   - CI 只跑 `ContainerSelfCheckTest`（纯 JVM，用假 `PathProbe` + `TemporaryFolder`）；
   - `VirtualEngine.selfCheck()` 依赖 Android `Context`、`NativeHookBridge`（native 注册）与 `DebugFlags`，
     **CI 不执行**，只在设备上由 `CmdReceiver` 的 `SELF_CHECK` 触发。
2. **临时状态必须清理**：S1/S3 的规则与文件都在 `finally` 里删；否则自检本身会污染运行中的容器。
3. **前缀必须与真实规则隔离**：用 `/selfcheck/in|out|w/`，不要复用 `/data`、`/sdcard` 前缀（会与真实规则互撞）。
4. **S3 绝不能写宿主，也不该写 `virtualRoot`**：
   - 目标路径必须来自 `translate()` 的结果，并断言 `startsWith(writeRoot)`；
   - `writeRoot` 是 **app 私有 `context.cacheDir/selfcheck`**，不是 `virtualRoot`——
     后者是用户/容器 Files 页面可见的目录，自检不该在其中留任何痕迹（2026-11-25 实机踩过：
     残留 `virtual/selfcheck/w` 目录，出现在 Files 页面里）。
   - 目标目录选哪里不影响验证力：只要路径经过 `translate()`，验的就是"规则翻译 + 真的能写"；
     "写入落沙箱"这条能力由探针 `J08/F01/F09/F14` 覆盖，容器自检不必重复证明。
5. **结果落两处**：`checks.log` 一行 `S selfcheck hookcheck ...`（可被 `COLLECT_OUT` 拉走）+ logcat `SELF_CHECK -> ...`。
6. 不要在自检里调用 `setupAppRedirections` / `removeAppRedirections`（会动真实 guest 的规则）。
