# 设计：native 重构与「临时适配器保契约」（2026-11-27）

- 回答：`native-hook.cpp` 7471 行该怎么拆、怎么保证 JNI/行为契约不变、分几批、每批验收什么。
- 用户 2026-11-27：**允许**为 native 重构加入**临时适配器**保证契约不变。
- 关联：`架构-C++测试夹具设计.md`、`架构-hook实现现状-代码为准.md`、`规则 10（等价检查）`

---

## 一、现状（实测）

| 项 | 数值 | 说明 |
| --- | --- | --- |
| `core/hook/src/main/cpp/native-hook.cpp` | **7471 行** | 单文件，143 个顶层函数 |
| `core/` 下已拆出 | `path_rules.{h,cpp}`(27+43)、`path_pattern.{h,cpp}`(33+52)、`diag_channels.inc`(31) | 只有"路径规则/模式/diag 通道"三块 |
| **JNI 导出** | **23 个** `Java_com_nextvm_core_hook_NativeHookBridge_*` ↔ Kotlin **23 个** `external fun`（1:1） | **这是硬契约**，重构不许改名/改签名 |
| CMake 源 | `native-hook.cpp` + `core/path_rules.cpp` + `core/path_pattern.cpp` | 新文件必须进 `CMakeLists.txt`（踩过：漏登记 → `undefined symbol`） |
| 现有 C++ 测试 | `path_pattern_test.cpp`、`path_rules_test.cpp` + golden 向量 | `cpp-core-test` job（ubuntu host g++，无需 NDK） |

### 职责聚类（143 个函数大致归 6 类）

| 类 | 代表函数 | 依赖 | 可纯逻辑化 |
| --- | --- | --- | --- |
| A 路径/映射合成 | `is_path_hidden` / `redirect_path` / `rewrite_virtual_path` / `synthetic_apk_path` / `synthetic_lib_path` | 仅有 `g_*` 路径字符串状态 | ✅ 高 |
| B `/proc` 内容伪造 | `maps_line_visible` / `transform_maps_line` / `build_spoofed_maps` / `build_spoofed_status` / `build_spoofed_stat` | 读文件 + 字符串 | ✅ 高（读文件可注入内容） |
| C seccomp 归因 | `sc_is_trapped_nr` / `sc_is_own_apk_path` / `sc_rewrite_fd_target` / `sc_app*` | 纯字符串/数字 | ✅ 高 |
| D GOT/PLT 与库过滤 | `patch_got_entry` / `install_hooks_callback` / `patch_library_got` / `patch_newly_loaded_libraries` | `dl_iterate_phdr`、内存写 | ⚠️ 中（过滤表可纯化，patch 动作不可） |
| E hook 入口 | `hooked_open(at)` / `stat` / `access` / `mkdir(at)` / `unlink(at)` / `rename` / `connect` / `getaddrinfo` | syscall/errno | ❌ 需真实 syscall |
| F JNI 注册/状态 | `JNI_OnLoad`、23 个 `Java_*`、`g_*` 状态 | JNI | ❌ 必须留门面 |

**结论**：A/B/C 三类（约 30–40 个函数、估计 1500–2000 行）可以抽成**不依赖 Android/JNI 的纯逻辑**，用现有 `cpp-core-test` 直接测；D 抽"过滤表/判定"，E/F 不动。

---

## 二、设计原则：门面不变 + 纯逻辑下沉 + 临时适配器

```
Kotlin external fun (23)  ──契约──►  Java_com_nextvm_core_hook_NativeHookBridge_*（门面，位置不变）
                                              │ 转发（临时适配器）
                                              ▼
                                   core/<domain>.cpp（纯逻辑，可 host 测试）
```

| 原则 | 做法 |
| --- | --- |
| **契约面冻结** | 23 个 JNI 名/签名、`JNI_OnLoad`、`is_container_diag_path`、`NS_*` 环境变量、日志格式、`diag_channels.inc` —— 一律不改 |
| **门面不搬** | `native-hook.cpp` 仍是唯一被 `CMakeLists` 编进 `nextvm-native` 的门面；新逻辑进 `core/`，门面**只转发** |
| **纯逻辑优先** | 新文件不得 include JNI/Android 头；入参出参用 `std::string`/`char*`/`std::vector`，便于 host g++ 编译 |
| **临时适配器** | 需要改签名/改结构时，门面里加转发层，标记 `// TODO(adapter: N-x)` + 退出条件；**同一提交里**写进本文件的「适配器台账」 |
| **每批一个 golden** | 新纯逻辑必须带 golden 向量（`core/hook/src/test/resources/golden/*.tsv`），C++ 与 Kotlin 共用格式 |

### 临时适配器的三种形式（按侵入度递增）

| 形式 | 用法 | 何时用 | 退出条件 |
| --- | --- | --- | --- |
| **A. 同名转发** | 门面函数体 = `return core::new_impl(args…)`；名字/签名不变 | 90% 的搬迁 | 下一批或收尾时删门面函数体里的旧代码 |
| **B. `extern "C"` 兼容符号** | 旧符号保留、内部改调新实现（给同文件其它调用者/`sc_*` 用） | 被同文件多处调用的工具函数 | 所有调用者迁完后删兼容符号 |
| **C. 宏/内联包装** | `#define old_fn new_fn` 或 `static inline` 薄壳 | 仅编译期切换、需要 A/B 对比 | 对比结束即删（**不允许留到 release**） |

**适配器台账（本文件维护）**：每加一个适配器记一行「文件:符号 / 形式 / 为什么 / 何时删 / 怎么验证」。

---

## 三、分批计划（每批 ≤2 职责，从纯逻辑到有依赖）

| 批 | 职责（最多两个） | 新文件 | golden | 验收 |
| --- | --- | --- | --- | --- |
| **N1** | ① 路径/映射合成（`synthetic_apk_path`/`synthetic_lib_path`/`rewrite_virtual_path`/`is_path_hidden`）② 路径隐藏判定与包名替换 | `core/path_spoof.{h,cpp}` | `golden/path_spoof_vectors.tsv`（输入路径 + 期望 out/true/false） | `cpp-core-test` 新测试；真机探针 N03–N09、W36 |
| **N2** | ① `/proc/self/maps` 行过滤与改写（`maps_line_visible`/`transform_maps_line`/`build_spoofed_maps`）② `status`/`stat` 内容生成 | `core/proc_spoof.{h,cpp}` | `golden/maps_vectors.tsv`（含真实 maps 片段） | `cpp-core-test`；真机 N10（`pkg_hit` 不退化） |
| **N3** | seccomp 归因（`sc_is_trapped_nr`/`sc_is_own_apk_path`/`sc_rewrite_fd_target`/`sc_app*`） | `core/seccomp_attr.{h,cpp}` | `golden/seccomp_nr.tsv`（system call 号表） | `cpp-core-test`；真机 `seccomp-diag.log` 格式不变 |
| **N4** | GOT/PLT 库过滤**判定表**（哪些库永不 patch / 系统库列表/自身库） | `core/lib_filter.{h,cpp}` | `golden/lib_filter.tsv`（库名 → patch/no-patch/reason） | `cpp-core-test`；真机 `NS_HOOK_SYSTEM_LIBS=0` 行为不变 |
| **N5（可选）** | 诊断通道/日志配额（已部分在 `diag_channels.inc`） | 沿用现有 | — | `check-diag-channels.mjs` |

**顺序理由**：N1 最纯（只依赖字符串），先建立"抽取—golden—适配器"的样板；N2 开始需要"输入 maps 内容"（可注入，仍是纯函数）；N3/N4 是"表 + 判定"，改动面小；E/F 类（hook 入口/JNI）**永远不搬**，只搬它们调用的纯逻辑。

---

## 四、契约校验（每批必跑）

### 4.1 源码级（毫秒，新工具）

新增 `tools/lint/check-jni-contract.mjs`：

1. 从 `NativeHookBridge.kt` 抽 `external fun` 的名字集合；
2. 从 `native-hook.cpp` 抽 `Java_com_nextvm_core_hook_NativeHookBridge_<name>` 导出集合；
3. 两边必须**逐一相等**（多/少都报错），并检查 `JNI_OnLoad` 存在、`CMakeLists.txt` 里 `native-hook.cpp` 仍被编进库。

> 这条能把"重构时漏改/改名/漏登记 CMake"从"设备上闪退"提前到毫秒级。

### 4.2 行为级

| 层 | 手段 | 判据 |
| --- | --- | --- |
| host | `cpp-core-test`（g++ 编 `core/*.cpp` + 测试） | 新 golden 向量全过；旧向量不回归 |
| 设备 | 探针 N01–N15 + W36/N10 | N 系列 `errno`/`pkg_hit` 与重构前一致（记录基线） |
| 设备 | 真机 via 启动 + 路径重定向 | `bind complete`、下载/保存路径仍在沙箱 |

---

## 五、风险与不做清单

| 风险 | 对策 |
| --- | --- |
| C++ 改动导致设备闪退（历史上 native 改动最难回滚） | 每批只搬**纯逻辑**；hook 入口（E 类）一行不改；每批单独 commit，可 `git revert` |
| 适配器永久留存 | 台账 + 退出条件；收尾提交里删除所有 `TODO(adapter: …)` |
| golden 与 Kotlin 向量不一致 | 沿用现有约定：同一 TSV 两端读；`gen-*` 脚本不参与 C++ |
| 改路径判定会动"欺骗"语义 | 设计理念第三条：只误导不危害；N1 的 golden 必须含"读写仍落在沙箱"的用例 |

**明确不做**：不动 E 类 hook 入口、不动 JNI 签名、不重命名 `.so`、不改 `NS_*` 开关语义、不把适配器留到 release。

---

## 六、备选方向：更臃肿/重复的 Kotlin 部分（对比）

| 候选 | 规模 | 重复/臃肿证据 | 风险 | 建议 |
| --- | --- | --- | --- | --- |
| `ActivityManagerProxy` | ~1300 行 | 与 `SystemServiceProxyManager` 有同类分发；`getStubForApp` 硬编码 | 中（Binder 全面） | 先做"重复检测报告"再拆 |
| `VirtualApplicationManager` | ~1700 行（已从 4589 降） | 剩余 `bindApplication` 编排（B4-4h 已计划收敛） | 中 | 按 B4-4h 收敛，不再搬出 |
| `VirtualActivityManagerService` / `VirtualPackageManagerService` | 各 300+ | 与真实 AMS/PMS 镜像代码重复 | 中 | 暂不动 |
| native | 7471 行单文件 | 143 函数、6 类职责混杂 | **高**（但用户已允许适配器） | **本轮推荐**（N1 起步） |

**推荐先做 native N1**：收益最大（单文件最大、纯逻辑最多），且有现成 host 测试基建；Kotlin 侧等 B4-4 收尾一起看。

---

## 七、临时适配器的性能影响（基于本仓库编译选项的评估）

### 7.1 事实基线（`core/hook/src/main/cpp/CMakeLists.txt`）

| 项 | 值 |
| --- | --- |
| 优化 | `-O2` |
| 特性 | `-fno-exceptions`、`-fno-rtti` |
| 可见性 | `hidden`（只导出 JNI） |
| LTO | **未开启** |
| 热路径 | `hooked_open/openat/stat/access/readlinkat`（每次 I/O 都经过） |

### 7.2 三种适配器的开销

| 形式 | 调用开销 | 说明 |
| --- | --- | --- |
| **A 同名转发（同 TU）** | **≈ 0**（-O2 内联） | 门面与新逻辑都在 `native-hook.cpp` 时 |
| **A 同名转发（跨 TU）** | **≈ 1–3 ns/次** | 一次普通 C++ 直接调用（hidden 可见性 → 无 PLT）；无 LTO 不会被内联 |
| **B `extern "C"` 兼容符号** | **≈ 1–3 ns/次** | 同上；不经过动态符号表（hidden） |
| **C 宏/内联** | **0** | 仅编译期 |

**与热路径对比**：一次 libc `openat` syscall 约 **1–2 µs**，一次 Java→JNI 调用约 **10+ µs**。
所以 30–40 个纯逻辑函数改成跨 TU 转发，最坏情况增加 **~0.1–0.3%**（相对 syscall），**可忽略**。

### 7.3 真正的性能风险不在"转发"，在"抽取时改变数据流"

| 红线 | 说明 | 本项目现状 |
| --- | --- | --- |
| 不新增堆分配 | `std::string` 返回、`std::vector` 临时对象是最常见的隐形开销（百 ns 级） | `redirect_path` 现在**已返回 std::string**；抽取时**保持签名**，不要改成"先拷贝再判断" |
| 不新增锁 | 规则表读取若已是原子/只读，不要在新层加 `std::mutex` | 保持原有访问方式 |
| 不在热路径加日志 | `__android_log_print` 是 µs 级 | N1–N4 的纯逻辑**不许**打日志；诊断走既有 `diag`/`fs_record` |
| 不引入异常 | `-fno-exceptions` 下 `throw` 直接编译失败 | 新文件沿用 `bool` 返回值 + `out` 参数风格 |
| 不改变返回值生命周期 | 返回 `const char*` vs `std::string` 语义不同 | 按原签名逐字搬 |

### 7.4 量化方法（每批必跑一次）

| 层 | 方法 | 门槛 |
| --- | --- | --- |
| host | 自写微基准（同一输入循环 1e7 次，`clock_gettime` 测 ns/call） | 重构后 **增幅 < 5%**，否则回退 |
| 设备 | `simpleperf record -e cpu-cycles` 对比重构前后 `hooked_open` 占比；或项目已有 `pt_*`（`pt_note_name`/`pt_emit`）计时打点 | `open` 路径占比变化 < 1 个百分点 |

**结论**：适配器本身**不会带来可感知性能影响**；只要守住 §7.3 五条红线，并把"微基准门槛"写进每批验收。

---

## 八、可复用的成熟项目 / 测试框架（调研结论）

### 8.1 Hook 库（只借鉴，不替换）

| 项目 | 定位 | 与本项目关系 | 结论 |
| --- | --- | --- | --- |
| [xHook](https://github.com/iqiyi/xHook)（爱奇艺） | Android PLT/GOT hook 库 | 与我们的 GOT/PLT patch 重叠；其"库过滤 / 延迟 patch"策略可对照 | **不替换**；N4 抽"库过滤表"时参考其规则 |
| [ShadowHook](https://github.com/bytedance/android-inline-hook)（字节） | Android **inline** hook | 我们不支持 inline hook；若将来要 hook 系统库内部调用是候选 | **暂不引入**（native 风险高、需评估 API 稳定性） |
| Dobby / bhook | inline hook | 同上 | 暂不引入 |

> 设计理念第 2 条：**机制保留**。现有 GOT/PLT + `NS_*` 开关 + diag 通道耦合很深，换库等于重写 hook 层，风险远大于收益。

### 8.2 C++ 测试框架（`cpp-core-test` 现状是裸 g++）

| 方案 | 成本 | 收益 | 结论 |
| --- | --- | --- | --- |
| **裸 `assert`（现状）** | 0 | 够用（现有 2 个测试） | 保留可跑 |
| **doctest**（单头） | 低：一个 `doctest.h` 放进 `test/cpp/third_party/` | 断言/分组/输出更好，编译快 | **推荐**（新增 N1–N4 用它写用例） |
| GoogleTest | 中：NDK 官方支持、`ndk-samples/unit-test` 有示例，但要构建 gtest | 生态最全 | 备选（若将来要设备端 instrumented 测试） |
| Catch2 | 中（编译更重） | 同 doctest | 不选 |
| Google Benchmark | 中（需构建） | 标准微基准 | **不引入**：§7.4 的微基准自写 20 行即可 |

### 8.3 "适合 JVM 测试的封装"（关键决策）

| 方案 | 做法 | 成本 | 结论 |
| --- | --- | --- | --- |
| **A. host 编译同一份纯逻辑**（现状） | g++ 编 `core/*.cpp` + 测试，跑在 `cpp-core-test` | 0（已有 job） | **推荐**：N1–N4 全部落这里 |
| B. JNI 薄封装 + 设备 instrumentation | `androidTest` 里 `System.loadLibrary` 后调 native | 高：要设备/模拟器 + 新 CI job | 不引入（项目无 instrumentation job） |
| C. 桌面 JVM 加载 host `.so` | 用 `-DUNDER_NDK=OFF` 思路为 host 编 `.so` 再 `System.load` | 中高：Android/desktop ABI 差异、JNI 头依赖 | 不引入（收益低） |

**结论**：**纯逻辑走 A（host g++），JNI 边界走契约工具 + 真机探针**：

- 23 个 JNI 导出 → `check-jni-contract.mjs`（毫秒级，防漏改/漏登记）[见 §四]；
- native 行为 → 探针 N01–N15 + W36/N10（设备）；
- 不做"为测而测"的 JVM 封装（规则 18.1：答不出固定什么行为就不写）。

### 8.4 采用清单（本轮）

| 采用 | 不采用 |
| --- | --- |
| doctest 单头（新用例） | xHook / ShadowHook / Dobby 替换 |
| 自写 host 微基准 + 5% 门槛 | Google Benchmark |
| host g++ 测纯逻辑（现有 job） | instrumentation / 桌面 JVM 加载 `.so` |
| `check-jni-contract.mjs` | Robolectric 模拟 native |

---
