# 计划：native 第一轮分割 —— `path_spoof`（N1a，2026-11-27）

- 目标：把 native 里**最纯、最该被测试固定、最可能因多实例改动**的一段逻辑搬出来，
  **不为了割而割**：每个候选先回答"割完能测什么 / 能改什么"。
- 前置：`docs/架构/架构-native模块关系与技术路线.md`（研究）、`docs/设计/设计-native重构与临时适配器.md`（适配器/性能）
- 状态：**准备（未改代码）**，等确认后执行

---

## 一、判据（"不为割而割"的四问）

| # | 问题 | 只有四问都答得出才割 |
| --- | --- | --- |
| Q1 | **割完能测什么**（host g++ 能跑的新用例）？ | 答不出 → 不割 |
| Q2 | **割完能改什么更容易**（参数化/替换/多实例）？ | 答不出 → 不割 |
| Q3 | **契约是否简单**（只需同名转发，不改 JNI/符号）？ | 要改签名才割 → 先评估适配器 |
| Q4 | **风险是否可控**（有 golden + 微基准 + 真机判据）？ | 答不出 → 延后 |

---

## 二、候选清单与决策

| 候选 | Q1 能测什么 | Q2 能改什么 | Q3 契约 | Q4 风险 | 决策 |
| --- | --- | --- | --- | --- | --- |
| `is_path_hidden` | 已在 `nextvm::path`，只剩 2 行薄壳 | — | — | — | ❌ **不割**（零收益） |
| `redirect_path` | `/data/user/0` ↔ `/data/data` 等价族兜底 | 规则表注入（已参数化） | 同名转发 | 低 | ✅ 割（职责②，很小） |
| `synthetic_apk_path` / `synthetic_lib_path` | 合成路径形状（token/包名/文件名） | **包名参数化**（多实例/多 guest） | 同名转发 | 低 | ✅ 割（职责①） |
| `rewrite_virtual_path` | base/split/lib/data/host-fallback 五类映射 | **包名参数化** | 同名转发 | 低 | ✅ 割（职责①） |
| `maps_line_visible` | 关键词隐藏表（nextvm/lsplant/dobby/…） | 关键词表可外部化 | 同名转发 | 低 | ✅ 割（职责①） |
| `transform_maps_line` | 一行 maps 的"隐藏/重写/原样"三态 | 包名参数化 | 同名转发 | 低 | ✅ 割（职责①，依赖上面三个） |
| `build_spoofed_maps` | 只测"纯转换"，读文件部分不可测 | — | 需拆成"读 + 转" | 中 | ⚠️ **本批不割**（下一批 N1b，拆成 `build_maps_from(content, pkg)`） |
| `build_spoofed_status` / `stat` | 同上（Name 行替换可测） | — | 需拆 | 中 | ⚠️ N1b |
| `hooked_open` 等入口 | 不可 host 测 | — | 不能改签名 | 高 | ❌ **永不割**（只搬它们调用的纯逻辑） |
| `sc_*`（seccomp） | 可测（N3） | — | 同名转发 | 中 | 下一轮 N3 |

**结论**：第一轮 = `synthetic_*` + `rewrite_virtual_path` + `maps_line_visible` + `transform_maps_line`（一个职责：**"伪装路径与 maps 行的纯转换"**）。
`redirect_path` 的等价族兜底作为**职责②**顺手纳入（它只有 8 行，且同属"路径翻译纯逻辑"）——**一批两个职责，不超**。

---

## 三、第一轮范围（N1a）

### 3.1 新文件

```
core/hook/src/main/cpp/core/path_spoof.h
core/hook/src/main/cpp/core/path_spoof.cpp
core/hook/src/test/cpp/path_spoof_test.cpp
core/hook/src/test/resources/golden/path_spoof_vectors.tsv
```

### 3.2 签名（**包名参数化**——这是"为未来可改"的核心）

```cpp
namespace nextvm::spoof {

// 合成"正常安装"路径（stock 形状）
std::string synthetic_apk_path(const std::string& guest_pkg);
std::string synthetic_lib_path(const std::string& guest_pkg,
                               const std::string& file_name);

// 把 betray 虚拟布局的路径改写成 stock 等价物；false = 无需改写
// host_fallback=true 表示"这是容器自己的路径，没有 guest 等价物"
bool rewrite_virtual_path(const std::string& guest_pkg,
                          const std::string& path,
                          std::string& out,
                          bool* host_fallback = nullptr);

// /proc/self/maps 行：false = 丢弃
bool maps_line_visible(const char* line);
bool transform_maps_line(const std::string& guest_pkg,
                         const char* line, std::string& out);

// 等价族兜底（/data/user/0 -> /data/data 重试）
std::string redirect_path_with_alias(
    const std::map<std::string, std::string>& rules,  // 或复用 path_rules 的类型
    const std::string& path);

}  // namespace nextvm::spoof
```

> `guest_pkg` 从全局 `g_spoofed_package_name` 变成**入参**：这是本次分割的"可改"收益——
> 未来多实例/多 guest 时，调用方传各自的包名，不必再动全局状态。

### 3.3 适配器（形式 A：同名转发，契约不变）

`native-hook.cpp` 里原函数体替换为：

```cpp
static std::string synthetic_apk_path() {
    return nextvm::spoof::synthetic_apk_path(g_spoofed_package_name);
}
static bool rewrite_virtual_path(const std::string& path, std::string& out,
                                 bool* host_fallback = nullptr) {
    return nextvm::spoof::rewrite_virtual_path(
        g_spoofed_package_name, path, out, host_fallback);
}
// transform_maps_line / maps_line_visible / redirect_path 同理
```

- 调用点（`hooked_open` / `hooked_readlink` / `build_spoofed_maps`）**一行不改**；
- 台账：`native-hook.cpp:synthetic_apk_path` 等 5 行，形式 A，N1b/收尾时删除转发。

### 3.4 golden 向量（host 与设备共用）

`path_spoof_vectors.tsv`（`#` 注释 + `op` 列）：

```
# op            guest_pkg    input                                                    expected                                   flag
synthetic_apk   mark.via     -                                                        /data/app/~~VmK8x2==/mark.via-VmK8x2==/base.apk  -
synthetic_lib   mark.via     libfoo.so                                                /data/app/~~VmK8x2==/mark.via-VmK8x2==/lib/arm64/libfoo.so  -
rewrite         mark.via     /data/user/0/com.nextvm.app/files/virtual/apks/mark.via_1.apk  /data/app/~~VmK8x2==/mark.via-VmK8x2==/base.apk  -
rewrite         mark.via     /data/user/0/com.nextvm.app/files/virtual/apks/mark.via_1_split_config.arm64.apk  .../split_config.arm64.apk  -
rewrite         mark.via     /files/virtual/data/mark.via_x/lib/arm64/libfoo.so (deleted)   .../lib/arm64/libfoo.so (deleted)  -
rewrite         mark.via     /data/data/mark.via/files/x                                -                                          unchanged
hidden          -            .../maps 行含 "nextvm"                                   -                                          drop
hidden          -            .../maps 行含 "libDexHelper.so"                          -                                          keep
transform       mark.via     maps 行含 guest apk 路径                                  .../base.apk                                rewrite
```

> 新增/改行为时**先改向量**（规则 18.1：测试固定行为）；向量由 `path_spoof_test.cpp` 与真机 `nativeGetHookStats` 的 `maps_lines/maps_hidden` 共同守护。

### 3.5 测试与验收

| 层 | 内容 | 判据 |
| --- | --- | --- |
| host 编译 | `g++ -std=c++17 core/path_spoof.cpp path_spoof_test.cpp`（现有 `cpp-core-test` 模式） | 通过 |
| golden | 跑 `path_spoof_vectors.tsv` | 全过 |
| 性能 | 微基准：`rewrite_virtual_path` 1e7 次 | ns/call 增幅 **< 5%** |
| 契约 | `check-jni-contract.mjs` | 24 ↔ 24 |
| 构建清单 | `CMakeLists.txt` 加 `core/path_spoof.cpp` | `backend` 编译通过（container-build） |
| 真机 | 探针 N03–N09、W36；`nativeGetHookStats` 的 `maps_lines/maps_hidden` 与插桩基线同量级 | 不退化 |
| 文档 | `docs/代码镜像/core/hook/cpp/core/path_spoof.md`（四节） | 同提交 |

---

## 四、效果预估（诚实）

| 项 | 变化 |
| --- | --- |
| `native-hook.cpp` | −约 120 行（这些函数体）；新增 5 行转发 |
| 可测性 | **新增约 12 条 host 用例**（合成路径/五类改写/maps 三态/等价族） |
| 可改性 | `guest_pkg` 参数化 → 多实例/多 guest 的路径伪装不再依赖全局 |
| 契约 | 零变化（同名转发；JNI/`.so`/符号不动） |
| 风险 | 低（纯字符串函数；有 golden + 微基准 + 真机探针） |

---

## 五、回退

```powershell
git -C _ref/ShitJumpingVM revert <N1a commit>
```

新文件删除 + 转发恢复为原实现；CMakeLists 回退。

---

## 六、明确不做（本批）

1. 不搬 `build_spoofed_maps/status/stat` 的**读文件部分**（N1b 拆"读 + 转"）；
2. 不动 `hooked_*` 入口、不动 JNI 签名、不动 seccomp；
3. 不改 `maps_line_visible` 的关键词表（只搬位置；改表属行为变更）；
4. 不引入 doctest（本轮先用现有裸 g++ + TSV；框架引入单独一批）。

---

## 七、下一步（N1b，预览）

把 `/proc` 内容生成拆成：
- `proc_spoof.h`: `build_maps_content(guest_pkg, real_content)`、`build_status_content(guest_pkg, real_content)`、`build_stat_content(...)`；
- `native-hook.cpp` 保留"读真实文件"的壳（I/O 不可测）；
- golden：用固定 `real_content` 片段 → 期望输出。
