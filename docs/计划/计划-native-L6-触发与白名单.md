# 计划：native L6（运行期加载库）的触发点与白名单（第 3 步，**仅计划**）

- 现状：**第 2 步已落地**（bind 之后 `+5s`/`+20s` 由 Kotlin 侧调 `nativePatchNewlyLoaded()` 触发一次延迟 patch）。
  本文件只规划**残余问题**，不含实现。
- 关联：`20261127-N2c-libs-applied.md`（第 1 步观测）、`20261127-N2c-step2-trigger.md`（第 2 步触发）、
  `架构-hook实现现状-代码为准.md` §六 #6（L6 缺口登记）
- 状态：**计划稿（未实现、未排期）**

---

## 一、残余问题（第 2 步之后仍存在的）

| # | 残余 | 现象 | 影响 |
| --- | --- | --- | --- |
| R1 | **`Runtime.nativeLoad` hook 从未装成功** | logcat / debug-trace 里没有 `installNativeLoadHook: SUCCESS`；上游本意让它当兜底触发点 | 只有"定时采样"这一个触发点，任何在 `+20s` 之后或采样间隔内加载的库仍漏 |
| R2 | **`+20s` 之后的加载** | 采样线程只跑两拍 | 加固壳"点开功能才 dlopen 解壳 so"的场景会被漏 |
| R3 | **`libnativeloader.so` 不 patch** | 它的 `dlopen` 不被 `hooked_dlopen` 拦截 | 同 R1/R2：没有实时触发点 |
| R4 | **无法区分"库被跳过"与"库压根没进扫描"** | `applied=0` 时只能靠 `late_libs`/`late_passes` 侧证 | 归因慢（这次查了两天） |

---

## 二、候选方案（成本 / 覆盖 / 风险）

| 方案 | 做法 | 覆盖 | 成本 | 风险 | 建议 |
| --- | --- | --- | --- | --- | --- |
| **A. 修 `Runtime.nativeLoad` hook** | 先诊断它为什么失败（符号名随 ART 版本变化 / `RegisterNatives` 签名不符 / 找不到 `libart.so` 符号），再修 | 覆盖所有 `System.loadLibrary`（含 `+20s` 之后） | 中（需读 ART 符号表） | 中（改的是 Java 方法实现替换，失败会走原路径，失败可见） | ✅ **优先**，因为它是上游本意、语义最正 |
| **B. `libnativeloader.so` 精确白名单** | 只 patch 它的 `dlopen`/`android_dlopen_ext` GOT，不整体打开 `NS_HOOK_SYSTEM_LIBS` | 覆盖真实 `dlopen` 调用（最全） | 中 | **中高**：动系统库是本项目两次崩溃的来源，必须真机回归渲染/popup/maps | ⏸ 备选，A 不够时再做 |
| **C. 加第三拍采样**（如 `+60s`、`+300s`） | 采样线程多打几拍 | 覆盖"较晚加载" | 极低 | 低（仅多调几次，幂等） | ✅ 可先做，作为 A 的兜底 |
| **D. 定期轮询**（每 N 秒一次，上限 T） | daemon 线程或 `Handler` 定时调 | 覆盖长尾 | 低 | 低（幂等；要注意 guest 生命周期与功耗） | ⏸ 作为 C 的替代，二选一 |
| **E. 打开 `NS_HOOK_SYSTEM_LIBS=1`** | 恢复上游"全 patch"形态 | 最全 | 极低 | **高（历史两次崩溃）** | ❌ **不做**（文档已列为"不可谈判"） |

---

## 三、若做 A：诊断步骤（先查不清不写码）

1. 在 `installNativeLoadHook` 每个失败分支补 `LOGE`（现在 6 个分支都有，但真机 logcat 只看到 I 级被吞的现象）→ 改走 `DebugTrace` 落文件（规则 20：重要过程必须落盘）；
2. 在真机上取一次 `run-as … grep 'nativeLoad' files/debug-trace.log`，确认卡在哪一支：
   - `cannot find java/lang/Runtime` / `libart.so not found` / `symbol not found`（ART 符号名变了）/ `RegisterNatives failed`；
3. 对 Android 15/16 的 ART，用 `nm -D libart.so | grep -i Runtime_nativeLoad` 得到真实符号（可离线在设备上跑 `run-as` + `toybox nm`？没有 nm 时用 `strings`）；
4. 修好后判据：`debug-trace` 出现 `installNativeLoadHook: SUCCESS`，且 `late_passes` 在每次 `System.loadLibrary` 后 +1。

> 注意：这条修好后会**自动**带来"实时触发"，第 2 步的定时触发可保留作兜底（不冲突，幂等）。

---

## 四、若做 B：白名单的风险控制（必须同时满足）

| 控制项 | 要求 |
| --- | --- |
| 白名单粒度 | 只对 `libnativeloader.so` / `libnativeloader_lazy.so` 两个文件生效，**不允许**出现"路径包含 /system 就 patch"这类宽规则 |
| 符号面 | 只 patch `dlopen` / `android_dlopen_ext` 两个符号，其余符号一律不 patch |
| 回归 | 渲染（RenderThread 不 abort）、via popup、探针全量、`maps_lines/maps_hidden` 量级 |
| 观测 | 新增 `skipped_syslib` 与 `applied` 对齐检查：patch 白名单库后 `applied` 应 +2 |

---

## 五、判据（任何一步做完都要过）

| 层 | 判据 |
| --- | --- |
| `hook stats` | `applied ≥ 1`、`slots ≥ 6`（probe guest）；via 仍为 `applied=0`（反向判据） |
| 探针 | `N03–N07` ok（当前全 `errno=2`）；`N10/N16` 的 `pkg_hit` 不退化 |
| 旧判据 | `libs=415 patched=411 skipped_self=1 skipped_system=3` 不变 |
| 能力 | via 启动到 `onActivityResumed`；渲染/popup 正常（用户实测判定） |
| 文档 | `现状-native-关键过程基线.md` 字段表 + `架构-hook实现现状-代码为准.md` §六 #6 状态同步 |

---

## 六、明确不做

1. **不**打开 `NS_HOOK_SYSTEM_LIBS=1`（方案 E）；
2. **不**为了覆盖 `libnativeloader` 而放宽图形栈六条关键词；
3. **不**在没拿到 `applied/slots` 数字前动任何白名单（否则无法判断效果）；
4. **不**把 L6 修复混进重构批（它是行为变更，必须单独文档 + 单独 CI + 真机回归）。

---

## 七、建议排期

| 顺 | 动作 | 前置 | 类型 |
| --- | --- | --- | --- |
| 1 | 第 2 步落地验证（`applied/slots` 变正、`N03–N07` 变 ok） | 第 2 步 CI + 装机 | 验证 |
| 2 | 方案 C（加第三拍 `+60s`） | 第 1 步的观测已在 | 行为变更（很小） |
| 3 | 方案 A（修 `nativeLoad` hook） | 上面的诊断步骤 | 行为变更 |
| 4 | 方案 B（精确白名单） | 仅当 A 仍不够、且有真实加固壳 guest 需要 | 行为变更（高风险） |
