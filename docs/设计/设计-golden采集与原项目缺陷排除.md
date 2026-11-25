# 方案：golden 只能来自原项目真机实测（含缺陷排除）

- 日期：2026-11-24
- 用户明确：**golden 只能依据原项目得到，包括原项目真机实测**；
  **除非原项目存在设计缺陷（那部分数据不参考）**
- 原项目定义：`https://github.com/woand/ShitJumpingVM` 近期状态（HEAD `63346e6`）

---

## 一、原项目的测试现状（实测）

| 项 | 结果 |
| --- | --- |
| `src/test`（任何语言） | **0 个** |
| 全仓 `test`/`spec` 目录 | **0 个** |
| `.github/workflows/android-ci.yml` | 只有 `:app:assembleDebug` + `lint`（`|| true`，不阻塞） |
| `releases/` | 一个 APK（`shitjump-v156-signed.apk`） |

**结论：原项目没有任何自动化测试，也没有 lint 门禁。**
所以"golden 来自原项目"必须靠**在原项目上植入诊断层 + 真机实测**。

---

## 二、原项目的已知设计缺陷（这些数据不参考）

### 2.1 缺陷 A：native hook 污染 GPU 驱动（最严重）

**证据**：`docs/输出/记录/记录-native-hook作用域与污染.md` §1

```
容器 hook 了 dlopen
  → 顺带 patch 了 /vendor/lib64/egl/libGLESv2_adreno.so、eglSubDriverAndroid.so
    → 这两个库的 stat / fopen / __system_property_get 被重定向
      → 驱动编译 shader 时拿不到正确的缓存/属性
        → RenderThread SIGABRT（栈顶 GrGLCompileAndAttachShader → libllvm-glnext）
```

**已修补**（`native-hook.cpp` 加了过滤）：
```cpp
// 2) 图形栈：永不 patch
if (libGLES || libEGL || eglSubDriver || libvulkan || vulkan.adreno || libllvm-glnext) return;
```

**对 golden 的影响**：
> **凡依赖真实渲染的 check 都不能作为 golden**，因为原项目在修补前就是崩溃的；
> 修补后的行为也只是"不崩"，不代表"渲染正确"。

### 2.2 缺陷 B：path fidelity 反复回退（上游自己承认 bootable 问题）

| commit | 内容 |
| --- | --- |
| `179cc3a v137` | register path-fidelity redirections before classloader build（**fix v136 startup crash**） |
| `7fdac6f v138` | synchronized native init before classloader build（**fix v136/v137 startup crash**） |
| `9a4b1bf v143` | remove v140 preset PathClassLoader（host-parent loader **split AppCompat runtime package**） |
| `8dbe2c3 v142` | **revert** framework LoadedApk path spoof to real paths（**bootable**） |

**对 golden 的影响**：
> path fidelity 相关（v135–v143 那套"路径保真"）在原项目里**反复导致不可启动**。
> 这部分 check 的"原项目数据"是**不稳定状态**，不能作为基线。

### 2.3 缺陷 C：trace 采样导致 guest 冻结

| commit | 内容 |
| --- | --- |
| `fc79d91 v153` | init trace **sampled**（1ms, 90s） |
| `f4c3ae5 v154` | init trace back to **full tracing**；**sampling reverted（froze guest）** |

**对 golden 的影响**：
> 任何"采样/埋点"机制都可能冻结 guest。**诊断层本身不能采样**（这印证了我们设计里
> 「`detail` 取消、只保留 check + stage」的取舍是对的）。

### 2.4 缺陷 D：WebView 渲染在 isolated 进程（容器完全不受控）

**证据**：`docs/架构/架构-hook关键路径.md` 失效点 #5

> WebView renderer 跑在 `sandboxed_process0`（isolated 进程），**任何 hook 都不生效**。

**对 golden 的影响**：
> WebView 渲染相关 check 在原项目和重构版里**都不可控** → 不能作 golden。

### 2.5 缺陷 E：签名填充缺失导致 NPE（已修）

| commit | 内容 |
| --- | --- |
| `5e90e03 v149` | fill PackageInfo.signatures for GET_SIGNATURES callers（**guest getKeyInfo NPE fix**） |

**影响**：若用 v149 之前的版本作基线，涉及签名的 check 会 NPE → 只能用 v149+。

---

## 三、可以作为 golden 的范围（明确边界）

基于上节，**排除**以下范围后，剩下的才进 golden：

| 排除 | 原因 |
| --- | --- |
| ❌ GPU / 渲染（Surface / EGL / GL / Vulkan / shader / RenderThread） | 缺陷 A |
| ❌ path fidelity 实验（v135–v143 那套） | 缺陷 B |
| ❌ WebView 渲染 / isolated 进程 | 缺陷 D |
| ❌ 依赖 trace 采样的行为 | 缺陷 C（采样会冻结 guest） |
| ❌ v149 之前的签名相关行为 | 缺陷 E |

**可参考的范围**（与本次 23 条 check 对照）：

| check | 能否作 golden | 理由 |
| --- | --- | --- |
| native.phase1 javaIoHooks | ✅ | 纯 Java 层，不涉渲染 |
| native.phase2 loadNativeLibrary | ✅ | 库加载，不涉渲染 |
| native.phase2 nativeInit | ✅ | GOT patch 计数，不涉渲染 |
| native.phase2 dataRoot | ✅（stage） | 只是设置前缀 |
| path.v142 registerRules | ✅ | 规则注册 |
| application.Step 1–6 | ✅ | 生命周期顺序（**核心可参考**） |
| activity.STEP 0–5 | ✅ | 类名/classloader/挂起注入（**核心可参考**） |
| ~~渲染相关~~ | ❌ | 缺陷 A |
| ~~WebView 渲染~~ | ❌ | 缺陷 D |

**代码里怎么落地这个"排除"**：诊断层的 path 字段区分 `native` / `path` / `application` / `activity` /
`render` / `webview`。**`render` 与 `webview` 产出的数据在对比时忽略**。

---

## 四、golden 采集流程（必须在原项目真机上跑）

### 4.1 前置：把诊断层植入原项目（只加不改）

原项目**没有** `DebugTrace` / `Diagnostics` → 需要连带植入：

| 需要植入的文件 | 来源 | 是否改原逻辑 |
| --- | --- | --- |
| `core/common/.../diagnostics/{CheckResult,Diagnostics,DiagnosticsRouter,FileSink,DiagnosticsHolder}.kt` | 我们新增 | ❌ 纯新增 |
| `core/common/build.gradle.kts` 的 `testOptions` | 我们新增 | ❌ 只加测试配置 |
| **23 处验证点**（`DiagnosticsHolder.check/stage`） | 我们的植入 | ❌ 只加调用，不改控制流 |
| `VirtualEngine.initialize` 的 Holder 安装 | 我们的植入 | ❌ 只加 6 行 |

**约束（写进流程守卫）**：植入后必须 `git diff` 检查 —— **只允许新增行，不允许修改/删除原行**。

### 4.2 采集

```
1) 在上游仓库（63346e6）植入诊断层 + 23 处验证点
2) 构建 → 装真机（10AFA8169Z0039G）
3) 启动 guest（mark.via）→ 走完三条关键路径
4) 导出：adb shell "run-as com.nextvm.app cat files/checks.log"
5) 过滤掉 path=render / path=webview 的行（缺陷 A/D）
6) 相对化路径（<ROOT>/<GUEST>）→ 人工 review
7) 提交为 golden：docs/现状/golden-baseline/<date>-upstream-checks.txt
```

### 4.3 对比

```
重构版同样植入 → 跑 → checks.log
逐行 diff：
  - 缺失行 -> 该步骤没走到
  - ok 变化 -> 行为变了
  - 顺序变化 -> 关键路径编排变了
```

---

## 五、与"静态等价检查"的分工

| 手段 | 证明什么 | 是否已完成 |
| --- | --- | --- |
| `check-refactor-equivalence`（对上游代码） | **逻辑逐字未变**（覆盖所有分支） | ✅ 8 个核心函数已等价 |
| **原项目真机 golden** | **运行时实际路径与结果** | ⏳ 本方案 |

**两者不重复**：
- 静态检查证明"代码一样"
- 真机 golden 证明"跑起来也一样"（包含 hook 是否真的挂上、native 是否真的 patch）

**但静态检查已覆盖"逻辑"这一层，真机 golden 的主要价值是"hook 实际生效"** —— 所以只需在
**重构版**上跑一次真机，与原项目**同样植入后跑的**结果对比；如果不想装两次原项目，
可以用"静态等价 + 重构版真机 + 排除缺陷范围"三角验证。

---

## 六、待确认

| # | 问题 | 默认处置 |
| --- | --- | --- |
| 1 | 是否需要真的装两次（上游 + 重构版）？ | 建议**只装重构版**，因为静态检查已证明逻辑等价，两份 golden 只会重复验证同一件事 |
| 2 | `render` / `webview` 的 check 要不要实现？ | 可以不实现 —— 原项目本身不可靠，实现了也无法当基线 |
| 3 | 上游植入后要不要提交回上游？ | 不提交，只在本地/CI 的临时分支 |
