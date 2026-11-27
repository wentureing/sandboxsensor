# 册：进程、slot 与跨应用调用（2026-11-27）

- **时效**：2026-11-27 复核。触发复核：① slot/租约实现变更 ② 多实例（M1–M8）启动 ③ 跨应用链路变更
- **可信度**：A=实测 / B=代码级 / C=推测
- **替代**：集中 `架构/架构-slot租约与进程术语.md`、`架构/架构-进程名与包名身份模型.md`、`架构/架构-recents多任务与guest会话收尾.md`、
  `设计/设计-process-slot测试分层与matrix.md`、`计划/计划-多实例支持缺口清单.md`、`计划/计划-process-slot缺陷登记-20261127.md` 的结论
- 一句话：**一个 slot = 一个进程，进程名可被多个 guest 复用（argv0 变最后 bind 的那个）**；所以"某 guest 是否在跑"只能靠 STOP/记录，不能靠 `ps`

---

## 一、现行结论

| # | 结论 | 等级 | 证据 |
| --- | --- | --- | --- |
| **R-a** | 身份三层：系统面进程名 `com.nextvm.app:pN`（不能改）/ 管理面 `$pkg:p$slot`（不进 guest 可见 API）/ **guest 可见 = 纯包名** | A | `架构-进程名与包名身份模型.md` |
| **R-b** | 顺序契约：`cacheProcessSlotFromCurrentName()` 必须**早于** `setArgv0`（改名不可逆） | A | 同上 |
| **R-c** | `slot` 0–9，一个 slot 一个进程；租约（lease）是"占槽凭证"，不是会话清单 | A | `架构-slot租约与进程术语.md` |
| **R-d** | 点 guest 复用原进程（pid 不变）；`STOP` 归还 stub（池可循环）；`STALE` 自动回收；按 **task** 逐个关闭（不杀进程） | A | `变更/20261127-process-slots-…` |
| **R-e** | `STOP` = 逻辑终止 + `SIGKILL`（`GuestProcessStopper`）；旧的 `killProcess` 只改记录不杀进程 | A | 同上 |
| **R-f** | 设置页计数 = 真实占用 slot 数（含 ORPHAN）；列表按 slot 一行、可展开 task | A | 同上 |
| **R-g** | **跨 guest `startActivity` 能被 VAMS 解析并派发到 stub**（`X1.cross_guest_start` = `dispatched`） | A | 探针 X1 |
| **R-h** | **派发成功 ≠ 目标进程活着**：`checks.log` 出现 `S slot released stubs slot=1 killed=1/1` 时，Activity 未进 `onCreate` | A | W-ext 首次失败（复现 2 次） |
| **R-i** | `bin.mt.plus` 崩溃点在 legacy `bindApplication` Step 2（`VAM.createApplication`） | A | logcat 原文 |

---

## 二、已知缺陷

| # | 缺陷 | 状态 |
| --- | --- | --- |
| **D9** | 起第二个 guest Activity 时 slot 进程被"释放+杀"（`killed=1/1`）⇒ Activity 不执行 | 复现 2 次；**绕过办法：重启容器后直接起**；待加 slot 生命周期观测 |
| L2 | 两实例共用 slot | 多实例缺口 M4（暂缓） |
| L11 | `Standard00` 与 guest 内部固定名重叠 | 已登记 |
| — | `candidatesFor` 曾未过滤已占用 stub（"New instance" 永远拿 `Standard00`） | 已修（`c0394e8`） |
| — | 进程名改写曾误伤主进程 | 已修（`4b01d3d`） |

---

## 三、待确认 / 可能错

| # | 事项 |
| --- | --- |
| 1 | D9 的触发条件是否只有"先跑 ProbeActivity 再起 WebViewProbeActivity"，还是更普遍的"同 guest 第二个 Activity" |
| 2 | `slot released stubs slot=1 killed=1/1` 是谁触发的（会话复用清理？STOP 的延迟效应？）—— 未定位到代码路径 |
| 3 | 跨应用（A→B）在**不同 slot** 时，task 归属与栈行为（只验证了派发） |
| 4 | 多实例 M1–M8 的代价（VPMS/代理按 instanceId 改键、合成路径带 instanceId）未量化 |

---

## 四、术语（避免混用）

```
guest        包名（com.sandbox.probe）
instance     <pkg>_<ts>（一次安装一个实例）
slot         0–9，一槽一进程
process      com.nextvm.app:pN（系统面）/ <pkg>:pN（管理面）
lease        占槽凭证（不是会话）
session      = task（一个 guest 可有多 task）
```

表述统一 `key=value`：`guest=… instance=… slot=… pid=… task=… tasks=… state=…`（不引入 `@`/`#`）。

---

## 五、取数

```powershell
# 谁在跑 / 占了几槽
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LIST
adb shell run-as com.nextvm.app grep -a '^S slot' files/checks.log | tail
# 跨应用派发（探针）
adb shell run-as com.nextvm.app cat files/virtual/data/<probe实例>/files/probe-report.json
```

---

## 六、原始文档索引

| 文档 | 用途 |
| --- | --- |
| `架构/架构-slot租约与进程术语.md` | **术语权威**（首选） |
| `架构/架构-进程名与包名身份模型.md` | 三层身份与顺序契约 |
| `架构/架构-recents多任务与guest会话收尾.md` | recents/多任务收尾 |
| `设计/设计-process-slot测试分层与matrix.md` | 测试分层 |
| `输出/变更/20261127-process-slots-会话复用与UI解绑.md` | 会话复用实现与实测（40 KB，证据最全） |
| `计划/计划-process-slot缺陷登记-20261127.md` | 缺陷登记 |
| `计划/计划-多实例支持缺口清单.md` | M1–M8（暂缓） |
