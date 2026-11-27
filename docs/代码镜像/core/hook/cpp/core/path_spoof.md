# 代码镜像：`core/hook/src/main/cpp/core/path_spoof.{h,cpp}`

> N1a（2026-11-27）从 `native-hook.cpp` 搬出。设计：`docs/计划/计划-native-N1a-path-spoof.md`；
> 适配器口径：`docs/设计/设计-native重构与临时适配器.md`。

## 职责

**做什么**
- 把"会 betray 虚拟布局的路径"改写成 stock 安装的等价物（合成 APK / 合成 lib / `/data/data/<pkg>/` 镜像）；
- `/proc/self/maps` 单行的三态判定：改写 / 原样 / 丢弃（含 hook 框架关键字过滤）；
- 规则重定向 + `/data/user/0` ↔ `/data/data` **等价族兜底**（两条路径指向同一目录）。

**不负责**
- ❌ 读文件（`real_open`/`fdopen` 仍在 `native-hook.cpp`：`build_spoofed_maps` / `read_whole_file`）；
- ❌ 全局状态（`g_spoofed_package_name` / `g_path_redirects` / `g_hidden_paths`）与 JNI：全部由门面绑定后**作为入参**传入；
- ❌ hook 入口（`hooked_open/stat/readlink/...`）与计数（`g_native_stats`）。

## 函数

| 函数 | 签名 | 作用 | 前提 / 副作用 |
| --- | --- | --- | --- |
| `synthetic_apk_path` | `std::string(const std::string& guest_pkg)` | 合成 `/data/app/~~<token>==/<pkg>-<token>==/base.apk` | 纯函数，无全局状态 |
| `synthetic_lib_path` | `std::string(const std::string& guest_pkg, const std::string& file_name)` | 合成 `…/lib/arm64/<file_name>` | 同上 |
| `rewrite_virtual_path` | `bool(const std::string& guest_pkg, const std::string& path, std::string& out, bool* host_fallback = nullptr)` | 五类虚拟路径改写；返回 false = 不需要改 | `out` 仅在返回 true 时有意义；`host_fallback=true` 表示"这是容器/引擎自己的路径，没有 guest 等价物"（maps 调用方应丢弃该行，readlink 调用方可用合成值兜底） |
| `maps_line_visible` | `bool(const char*)` | 命中 hook 框架关键字（`nextvm`/`lsplant`/`dobby`/`bhook`/`xhook`/`substrate`/`xposed`）→ false | 关键字表是**行为契约**的一部分，改表 = 行为变更 |
| `transform_maps_line` | `bool(const std::string& guest_pkg, const char* line, std::string& out)` | 一行 maps 的三态：改写（`prefix+rewritten+"\n"`）/ 原样 / 丢弃（返回 false） | 依赖 `rewrite_virtual_path` + `maps_line_visible` 的先后顺序：**先路径改写，再关键字过滤** |
| `redirect_path_with_alias` | `std::string(const nextvm::path::RuleTable& rules, const std::string& path)` | 先 `path::redirect_path(rules, path)`；未命中且以 `/data/user/0/` 开头时用 `/data/data/…` 重试一次 | 空串 = 未命中（调用方原样使用输入）；**只兜底一次，不递归** |

## 偏差登记

| # | 偏差（相对 `native-hook.cpp` 原实现） | 类型 | 说明 |
| --- | --- | --- | --- |
| 1 | `guest_pkg` 从全局 `g_spoofed_package_name` 改为**入参** | ✅ **必要** | 抽取的核心收益：多实例 / 多 guest 时调用方传各自包名，不再依赖全局状态。语义等价 —— 门面每次调用传当前全局值 |
| 2 | 合成路径的 token `VmK8x2==` 提为常量 `kInstallToken` | ✅ 中性 | 字面量不变，行为不变 |
| 3 | `/data/user/0/` 判定：`std::strncmp` → `std::string::compare(0, 13, …)` | ✅ 中性 | `compare` 在 `size() < 13` 时比较长度自动截短，与原 `strncmp` 遇 `'\0'` 提前结束的结果一致（都不相等） |
| 4 | `redirect_path` 的等价族兜底从 `native-hook.cpp` 移入本文件（`redirect_path_with_alias`） | ✅ 中性 | 两段式语义、`空串=未命中` 契约、只兜底一次，均逐字保留；门面仍保留 `path == nullptr \|\| path[0] == '\0'` 的前置检查 |
| 5 | `rewrite_virtual_path` / `transform_maps_line` 的三分支顺序、`(deleted)` 后缀、`lib/` 判定 | ✅ 无变化 | 逐字搬迁，只把 `g_spoofed_package_name` 换成 `guest_pkg` |

## 约束与坑

1. **不得 include** `<jni.h>` / `<android/*>` / `<link.h>` / `<sys/*>` —— 一旦引入，host 侧 `cpp-core-test` 就编不过（`架构-C++测试夹具设计.md` §一）。
2. **新 `.cpp` 必须登记进 `core/hook/src/main/cpp/CMakeLists.txt`**，否则 Android 侧 `undefined symbol`（本项目已踩过一次）。
3. `maps_line_visible` 的关键词表**属于契约**：想隐藏新的框架特征，先改 `golden/path_spoof_vectors.tsv`，再改表（规则 18.1）。
4. 热路径（每次 I/O 的 `redirect_path`）：本文件**不许**加日志 / 加锁 / 新增分配；`std::string` 返回值签名保持原样（`设计-native重构与临时适配器.md` §7.3）。
5. 单测是 `core/hook/src/test/cpp/path_spoof_test.cpp` + `src/test/resources/golden/path_spoof_vectors.tsv`，
   CI job `cpp-core-test` 里编译 `path_rules.cpp + path_spoof.cpp + path_spoof_test.cpp`（`redirect_path_with_alias` 依赖 `path_rules`）。
6. 真机判据：`nativeGetHookStats` 的 `maps_lines` / `maps_hidden` 与 `现状-native-关键过程基线.md` 同量级；`spoof=0` 属正常（见该文 §四）。
7. `guest_pkg` 为空串时行为：合成路径会变成 `/data/app/~~VmK8x2==/-VmK8x2==/base.apk`（不崩、无回退），
   但**没有任何调用方这样传** —— 门面传的是 `g_spoofed_package_name`，未设置时为空串，此时 maps 里不会出现 guest 路径，
   所以这条路径实际不可达；若将来要防，可在门面加 early-return，不要在本文件加分支（保持纯函数简单）。
