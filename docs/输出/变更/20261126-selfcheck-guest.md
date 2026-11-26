# 变更：SELF_CHECK 覆盖主进程 + guest 进程

- 日期：2026-11-26
- 授权：用户「通过 SELF_CHECK 在 guest 进程内跑，要求自检覆盖主进程和 guest」
- 背景：S7（真实规则抽样）在主进程必然 `skipped` —— 规则在 guest 进程注册（v142 教训），
  所以主进程自检只能证明"机制可用"，证明不了"这个 guest 的规则真的生效"

---

## 一、目标

1. `SELF_CHECK` 一次触发，**主进程 + 指定 guest 进程**各跑一遍自检；
2. guest 侧自检能拿到**真实规则表**（S7 真检）、真实 `debug-trace.log`（S8 真检）、guest 的 `cacheDir`；
3. 两侧结果都落到**同一个 `checks.log`**（`S selfcheck …` / `S guestselfcheck …`），可一次 `COLLECT_OUT` 拉走；
4. 不改变 guest 启动路径的开销：只有存在请求文件时才跑。

## 二、机制（文件触发 + 首次 resume 消费）

```
CmdReceiver SELF_CHECK --es instance <instanceId>
   ├─ VirtualEngine.selfCheck()      主进程自检（现状，8 项）
   └─ 写 <guestFiles>/selfcheck.request（时间戳）
          ↓  guest 下次/当前 resume
   VirtualApplicationManager.onActivityResumed（每进程一次）
       └─ SelfCheckRequest.consume(dir) == true
            └─ GuestSelfCheck.run(...)  →  S guestselfcheck … 写 checks.log
```

为什么不用广播：guest 与容器同 uid 同包，动态广播会同时投递到两个进程，需要额外的进程判定；文件 + 首次 resume 更简单、可断点（未启动的 guest 下次启动时消费）。

## 三、影响范围

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `diag/SelfCheckRequest.kt` | 新增 | 请求文件协议（写/查/消费），**纯 JVM 可测** |
| `diag/GuestSelfCheck.kt` | 新增 | guest 侧组装 `ContainerSelfCheck`（PathProbe 走 guest 的 NativeHookBridge） |
| `diag/ContainerSelfCheck.kt` | 修改 | 加 `confRequired`：guest 侧读不到 `path_spoof.conf` 时 S6 记 skipped 而不是 fail |
| `lifecycle/VirtualApplicationManager.kt` | 修改 | `onActivityResumed` 首次消费请求（每进程一次） |
| `engine/VirtualEngine.kt` | 修改 | `selfCheck(guestInstanceId: String?)`：跑主进程自检 + 写请求 |
| `app/CmdReceiver.kt` | 修改 | `SELF_CHECK` 支持 `--es instance` |
| 测试 | 新增/修改 | `SelfCheckRequestTest`（新）、`ContainerSelfCheckTest` 加 S6 可选用例 |
| 镜像文档 | 新增 | `docs/代码镜像/core/virtualization/diag/{SelfCheckRequest,GuestSelfCheck}.md` |

## 四、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | 在 `onActivityResumed` 加逻辑拖慢每个 resume | 只在**首次** resume 检查一次（`@Volatile` 门闩），且 `consume` 是一次 `exists+delete` |
| 2 | guest 侧 S1/S3 注册临时规则会与真实规则表混在一起 | 用 `/selfcheck/*` 前缀（与真实族隔离），`finally` 删除 |
| 3 | guest 侧写 `debug-trace.log` 会污染主进程诊断文件 | guest 的 `app.filesDir` 是**沙箱内**路径，与主进程 `context.filesDir` 不同文件 |
| 4 | `confRequired=false` 让 S6 形同虚设 | 只在 guest 侧关闭；主进程仍强制要求 |

## 五、回退

```powershell
git -C _ref/ShitJumpingVM revert <sha>
```

## 五之一、**仅 debug 包**（用户 2026-11-26 要求）

自检在三个入口显式门控，release 包里等于不存在：

| 入口 | 门控 | release 行为 |
| --- | --- | --- |
| `VirtualEngine.selfCheck(guestInstanceId)` | 函数第一行 `if (!DebugFlags.isDebugBuild()) return "hookcheck skipped(release build)"` | 不跑 8 项、不写 `checks.log`、**不写请求文件** |
| `GuestSelfCheck.run(...)` | 第一行同样判断 | 直接返回（防御：即使有请求文件也不跑） |
| `VirtualApplicationManager.onActivityResumed` 的消费 | `!guestSelfCheckDone && DebugFlags.isDebugBuild()` | 不 `consume`、不做文件 IO |

`SelfCheckRequest` 本身**不做门控**（保持纯文件协议、纯 JVM 可测），门控一律在调用点。

## 六、验证

| 层 | 判据 |
| --- | --- |
| 单测 | `SelfCheckRequestTest`（写/查/消费/重复消费）、`ContainerSelfCheckTest` C10（S6 可选） |
| CI | `module-test :core:virtualization` 绿 |
| 真机 | `SELF_CHECK --es instance <id>` → `checks.log` 同时出现 `S selfcheck` 与 `S guestselfcheck`；guest 侧 **S7 不再是 skipped**（规则表非空） |
