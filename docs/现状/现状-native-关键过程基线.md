# 现状：native 关键过程基线（2026-11-27，干净安装实测）

- 设备：`10AFA8169Z0039G`（vivo V2463A / Android 15 / arm64-v8a）
- 容器：**`1.0.0-4f0e9d7`**（vc = run 号；容器代码 `6324660`，含 native 插桩 + 运行期采样）
- 安装方式：`clean-install-and-test.ps1`（卸载旧包 → 装机 → appops → 导入 via/probe）
- 探针：`debug.probe.suite=all`
- 取数命令：`adb shell run-as com.nextvm.app grep -a 'Dbg/native' files/debug-trace.log`

---

## 一、冒烟结果（干净安装）

| 项 | 结果 |
| --- | --- |
| 安装 | `1.0.0-4f0e9d7` |
| appops | allow |
| 容器就绪 | 10s + 私有目录可读 |
| 导入 | `mark.via_1795746372168` / `com.sandbox.probe_1795746372452` |
| SELF_CHECK | `ok=8 fail=0` |
| 探针 | `entries=98 fail=2 ok=53 skipped=43`（fail = J26/J27，设计故意） |
| via | `bind complete (framework path)` + `onActivityResumed` |

---

## 二、插桩数据（两个 guest × 三个时刻）

| guest | 时刻 | libs | patched | skipped_self | skipped_system | late_passes | late_libs | spoof | maps_lines | maps_hidden |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `com.sandbox.probe` | bind | 415 | 411 | 1 | 3 | 0 | 0 | 0 | 0 | 0 |
| `com.sandbox.probe` | +5s | 415 | 411 | 1 | 3 | 0 | 0 | 0 | 0 | 0 |
| `com.sandbox.probe` | +20s | 415 | 411 | 1 | 3 | 0 | 0 | 0 | 0 | 0 |
| `mark.via` | bind | 415 | 411 | 1 | 3 | 0 | 0 | 0 | 0 | 0 |
| `mark.via` | +5s | 415 | 411 | 1 | 3 | 0 | 0 | 0 | 0 | 0 |
| `mark.via` | +20s | 415 | 411 | 1 | 3 | 0 | 0 | 0 | 0 | 0 |

**两个 guest 完全一致**，三个时刻也一致（bind 之后没有库动态加载，也没有 native 读者触发 `/proc` 伪造）。

---

## 三、字段判据（后续每批重构对照）

| 字段 | 当前值 | 判据 |
| --- | --- | --- |
| `skipped_self` | **1** | **必须保持 1**（只能跳过 `libnextvm-native.so`；>1 说明误伤） |
| `skipped_system` | **3** | **必须保持 3**（linker / vdso / libart / libhwbinder 中固定命中 3）；变化要解释 |
| `patched = libs − skipped_self − skipped_system` | 411 = 415−1−3 | 恒等式；出现不等说明有未统计的早退分支（插桩缺口） |
| `libs` / `patched` | 415 / 411 | **允许随版本漂移**（加载库集合随 ROM/版本变）；只记量级 |
| `late_passes` / `late_libs` | 0 / 0 | 启动后无 dlopen；若某 guest 出现非 0，说明运行期加载了新库（正常） |
| `spoof` / `maps_lines` / `maps_hidden` | 0 / 0 / 0 | **当前为 0 且有解释**（见 §四）；用于"native 读者"场景 |
| `skipped_self_host` | **0**（首取 2026-11-27 / `1.0.0-bdc71c5`） | N2-B 新增：`patch_library_got` 里因路径含 `com.nextvm.app` 被跳过。**不并入** `skipped_self`（后者必须恒为 1） |
| `skipped_graphics` | **7**（首取同上） | N2-B 新增：图形栈六条关键词命中数。`NS_HOOK_SYSTEM_LIBS=1` 时**仍应计数且仍跳过** |
| `skipped_syslib` | **404**（首取同上） | N2-B 新增：`NS_HOOK_SYSTEM_LIBS=0` 下被跳过的其余系统库（与 callback 层的 `skipped_system` 不同义） |

> **N2-B 起 `hook stats` 行有 12 个 key**（新增 3 个）。判据：`skipped_self` / `skipped_system` / `patched`
> 三个**旧判据一律不变**（真机实测仍然 1 / 3 / 411）。
>
> **首取观察（2026-11-27，`mark.via` 与 `com.sandbox.probe` 相同；bind / +5s / +20s 三拍也相同）**：
> `skipped_self_host=0`、`skipped_graphics=7`、`skipped_syslib=404`，而 **`7 + 404 + 0 = 411 = patched`**。
> 即"进入 `patch_library_got` 的 411 个库全部被过滤掉" ⇒ 这两个 guest 进程里
> **`kAppLib`（应用/沙箱库）命中为 0**，`patched` **不等于**"真的 patch 了几个库"。
> 该观察同时印证 `20261127-N2a-lib-filter.md` §六 记的"`libs_patched` 语义偏松"，并与 `late_passes=0`（延迟 patch 从未跑）自洽。
> **待办（N2-C）**：给 `kAppLib` 也加计数（例如 `libs_applied`），把
> 「扫描 / 过 callback / 过过滤 / 真的 patch」四级数字分开；在那之前**不要**用 `patched` 推断"patch 了多少库"。

---

## 四、为什么 `spoof/maps_*` 是 0（不是插桩坏了）

三个事实：

1. `make_proc_spoof_fd(PROC_SPOOF_MAPS)` 只在**经 libc PLT 读 `/proc/self/maps`** 时触发；
2. `mark.via` 是纯 Java 应用，启动期**没有 native 读者**；
3. 探针的 N 系列（`libprobe.so`）刻意用 **raw syscall** 测真实视角（`N09.syscall_openat` 就是这类），Java 侧 `ProbeRunner` 读 maps 走 Java 流（不经 PLT）→ 两者都不触发 hook。

> 结论：**插桩工作正常**；`spoof=0` 反映"本轮没有 native 读者"，不是漏插桩。
> 要覆盖 `spoof/maps_*`，需要一个**经 libc 读 `/proc/self/*` 的 native 读者**：加固壳 guest（bin.mt.plus / YESPDF）或探针新增一个"刻意经 libc 读 maps"的项。

---

## 五、用途

1. **后续 native 重构（N1a 起）每批对照本表**：`skipped_self/skipped_system` 不得变化，`libs/patched` 只作量级参考；
2. **插桩三阶段**（bind / +5s / +20s）证明采样线程正常；若某段缺失 → 采样调度回归；
3. **`spoof=0` 的语义**写进判据：出现非 0 时先确认"谁在读"（native vs Java），再判是否 spoof 行为变化。

---
