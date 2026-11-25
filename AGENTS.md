# 项目约定（sandboxsensor）

> 本文件优先级高于全局 `~/.dsh/AGENTS.md`。冲突时以本文件为准。

## 仓库与 CI

| 仓库 | 角色 |
| --- | --- |
| `wentureing/sandboxsensor` | 主仓库：探针 + 文档 + **全部 CI** |
| `wentureing/ShitJumpingVM` | 容器骨架，只放代码，**无 CI** |

- 分支：`main`
- 所有 workflow 只在本仓库（`build-probe`、`build-container`）
- 产物必须是 `.apk`，走 **Release asset**（tag `apk-latest`），不用 artifact（会被强制 zip）

## 推送：两种方法

### 方法 1（常规，默认）：SSH

```powershell
# remote 必须是 SSH URL
git remote set-url origin git@github.com:wentureing/sandboxsensor.git

# 每次都必须在同一条命令里带上 GIT_SSH（每次调用都是新进程）
$env:GIT_SSH='C:\Windows\System32\OpenSSH\ssh.exe'; git push origin main
```

判断：

- `Hi <user>! You've successfully authenticated` → 通道正常（exit=1 是正常的，GitHub 不给 shell）
- `Connection timed out`（22 或 443）→ 网络问题，换方法 2
- `Permission denied (publickey)` → 密钥问题，报告用户

### 方法 2（备选）：token + HTTPS

仅在 SSH 不通时使用。脚本已就绪：`tools/git/push-https.ps1`

```powershell
git remote set-url origin https://github.com/wentureing/sandboxsensor.git
.\tools/git/push-https.ps1            # 默认推 main
```

约束：

- token 从 `token.txt` 读取（该文件在 `.gitignore` 里），**脚本不打印 token、不写进 URL、不做持久化配置**
- 用 `GIT_CONFIG_COUNT/GIT_CONFIG_KEY_0/GIT_CONFIG_VALUE_0` 传 `Authorization` header
- 必须加 `-c http.sslBackend=openssl`（受限环境 schannel 拿不到凭据）
- 若报 `Recv failure` / `Connection was reset` → 网络仍不通，需先修网络或代理

### 故障排查顺序

1. `ssh -T git@github.com`（8s 超时）→ 通就**用方法 1**
2. 不通 → `.\tools/git/push-https.ps1` → 通就**用方法 2**
3. 两者都不通 → 先修网络（本机代理 `127.0.0.1:12450`），再回到 1

## 接管控制时的硬约束

**不要破坏现场。** 任何会改变设备/应用状态的操作必须先获得许可：

| 动作 | 是否默认允许 |
| --- | --- |
| `adb logcat -d -b all`（只读 dump） | ✅ 允许 |
| `adb shell pidof` / `dumpsys` / `getprop` | ✅ 允许 |
| `adb shell input keyevent HOME` | ❌ 禁止（会打断用户定位） |
| `adb shell am force-stop` | ❌ 默认禁止 |
| `adb shell pm clear` / `adb uninstall` | ❌ 默认禁止 |
| `adb logcat -c`（清缓冲） | ❌ 默认禁止（丢失证据） |

脚本侧：`auto-test.ps1`、`collect-probe.ps1`、`diag-boot-loop.ps1` 均带**复位守卫**，不带 `-Reset` 会直接返回。
需要复位时显式传 `-Reset`。

采集一律用：`adb logcat -d -b all -v threadtime > file`（不带 `-c`）。

## 设计理念（改动前第一道尺子）

> **`docs/设计/设计-项目设计理念.md`** —— 三条理念，与任何改动冲突时，要么改改动，要么先改这份文档。

1. **有条理、技术易实现** —— 三源交叉（AOSP 源码看行为 / 设备 dex 看谁真 native / 日志看谁没生效）、
   契约驱动（先列调用方假设）、先写测试再改、每层独立计数。
2. **充分利用上游代码** —— 先问「上游有没有现成实现」。上游的 **bug 修参数**，上游的 **机制保留**。
   反例：看到 `external_data`/`obb` 目标错就想全部重写，其实规则映射机制是对的。
3. **路径欺骗：看起来真实、用起来重定向、骗不过就放弃** ——
   需要骗的（含包名的 Context 路径）合成；不需要骗的（`Environment`/`MediaStore`/`StorageVolume`，
   本来就不含包名）一律不改。**硬约束：只误导，不危害读写** —— 宁可不骗，也不能把 guest 弄坏。
## 路径合成：syntheticDataDir / pathFidelity

> **改 VirtualConstants.synthetic* 或 PATH_FIDELITY_WHITELIST 之前，必读**
> **docs/架构/架构-syntheticDataDir.md** —— 那里写了用途、哪些 API 返回合成路径、
> 哪些不需要合成（Environment / MediaStore）、白名单现状、单实例缺陷与性能代价。

## 排查问题的第一站：GuestBreadcrumb（不要跳过）

> **遇到「guest 行为异常但没崩溃 / 没栈 / 没日志」时，先看 breadcrumb，再决定往哪查。**

上游 NEXTVM 已在全链路埋了 **199 处** `GuestBreadcrumb`，它记录**有序、带时间戳、含被吞异常完整栈**的 guest 生命周期事件。专为"静默失败"设计。

| 取法 | 命令 / 路径 |
| --- | --- |
| logcat（最快） | `adb logcat -d -b all -v threadtime \| Select-String "BootCrumb"` |
| 文件（含完整栈） | `/data/user/0/com.nextvm.app/files/virtual/data/<instance>/files/breadcrumb-<pid>.log` |

**推荐排查顺序**：

```
1. BootCrumb 日志                              ← 先看这个
2. breadcrumb-<pid>.log 末尾                    ← 找"最后一步"+"被吞的异常"
3. 按症状分层：窗口/输入/路径/检测面（对应探针 P/N 系列）
4. 最后才看 native 栈（tombstone / bugreport）
```

**为什么会绕弯路**（本项目已发生的教训）：

| 症状 | 我们的错误路径 | breadcrumb 能省的 |
| --- | --- | --- |
| RenderThread SIGABRT | 一上来归因"Adreno 驱动缺陷"（查 Flutter/Chromium workaround），实际是容器 hook 污染 GPU 驱动 | 直接看到 guest 启动到哪一步 |
| 长按 ANR | 猜 `WindowManager`/`PopupWindow`/`Window.Callback` | 显示长按后走进哪个生命周期回调 |
| Via 主页源码 | 猜 MIME / 猜 WebView patch，实际是渲染在 isolated 进程 | 至少先排除 guest 启动链路问题 |

**一句话**：栈顶在驱动里，不代表病因在驱动里；breadcrumb 是正向记录过程，比从结果倒推可靠。

详细用法见 `docs/输出/调试指南.md`。

## 文档结构：五类目录 + 错误结论归档

**（2026-11-24 重组。旧路径 `docs/records/`、`docs/changes/`、`docs/调研/` 已不存在）**

```
docs/
├── README.md     总索引 + 写作约定
├── 计划/         要做什么、什么顺序
├── 架构/         怎么运作（机制/边界/坑）
├── 设计/         为什么这么选（选型/路线取舍/规范）
├── 现状/         现在什么生效、什么没生效（事实快照）
├── 输出/         做过什么（记录/变更/调研/调试手册/操作）
├── 代码镜像/     **镜像代码目录结构，一文件一文档**（规则 7）
├── archive/      **错误结论归档**
└── diagrams/
```

### 硬性规则

| # | 规则 |
| --- | --- |
| 1 | **结论必须带实测证据**（探针编号 / logcat 片段 / 反汇编结果），不能只有推理 |
| 2 | **被推翻的结论不删**：移到 `docs/archive/错误结论-<年月>/`，并在该目录 `README.md` 登记「原结论 / 被什么推翻 / 现在看哪里」 |
| 3 | **引用任何结论前先对照 `archive/`** —— 里面每条都写明它错在哪 |
| 4 | 一份文档只回答一个问题；跨主题就拆 |
| 5 | 文档间引用用相对路径；移动文档时同步更新引用 |
| 6 | 能力状态变化（✅/❌）必须在同一提交里更新 `docs/现状/README.md` 的「能力生效状态」表 |
| 7 | **重构/拆分代码时，必须为每个新文件产出「同目录结构 + 同命名」的说明文档**（见下节） |

### 规则 7：代码镜像文档（重构的强制产物）

**任何代码分割/重构，新文件的说明文档必须与代码**同目录结构、同命名**：**

```
代码                                              →  文档
core/virtualization/.../lifecycle/GuestPathSetup.kt  →  docs/代码镜像/core/virtualization/lifecycle/GuestPathSetup.md
core/hook/JavaIoHooks.kt                          →  docs/代码镜像/core/hook/JavaIoHooks.md
core/hook/cpp/hooks_io.cpp                        →  docs/代码镜像/core/hook/cpp/hooks_io.md
```

即：`docs/代码镜像/<与仓库一致的相对路径>/<同名>.md`（扩展名换成 `.md`）。

### 拆分的判据（重要：不看行数）

> **不要为了压缩而压缩。职责集中、命名确切即可。**

| 拆分的**依据** | 说明 |
| --- | --- |
| **职责是否单一** | 一个文件只回答一个问题。两个职责混在一起 → 拆 |
| **命名是否确切** | 文件名能准确说出"这个文件只干这件事"；说不清 → 说明还没想清职责 |
| **依赖方向是否清晰** | 契约文件被补充文件依赖；补充文件之间不互相依赖 |

| ❌ 不是拆分依据 | 说明 |
| --- | --- |
| "行数超过 1000" | 只是**可疑信号**，提示"可能职责不单一"，需要人工判断；不是达标线 |
| "注释太多" | 注释该搬文档（规则 8），不需要拆文件 |
| "拆完看起来更小" | 大小不是目标 |

**反例（本项目踩过）**：为了把 `NativeHookBridge` 压到 1000 行以下而拆；
拆完行数达标了，但剩下的部分（门面 + JNI 声明 + 规则组合 + fake content）**职责仍然混杂**——
说明那次拆分的动机错了。

**正确做法**：先问"这个文件里有几种职责"，按职责拆，命名跟着职责走；
拆完行数是多少都接受。

**每份镜像文档固定四节**：

| 节 | 内容 |
| --- | --- |
| **职责** | 这个文件负责什么、**不负责什么**（边界比职责更重要） |
| **函数** | 每个公开函数：签名 + 一句话作用 + 关键副作用/前提条件 |
| **偏差登记** | **与 AOSP / 原实现不一致的地方**：偏差内容、为什么偏离、是**必要偏差**还是**待修偏差**、如何观测 |
| **约束与坑** | 调用顺序要求、线程/进程限制、已知缺陷、为什么这样写 |

### 「偏差登记」的判据（重要）

> **与 AOSP 不一致不一定是错误。容器的目标是让 guest 可用，不是复刻 AOSP。**

判据是四问，而不是"是否与 AOSP 相同"：

1. 这个偏差**是否必要**（容器原理决定，去掉就无法工作）？
2. 偏差**是否有记录**（为什么偏离）？
3. 偏差**是否可观测**（有日志/统计能查到）？
4. 偏差**是否破坏 guest 可用性**（真实应用是否变差）？

| 类型 | 处置 |
| --- | --- |
| **必要偏差**（Deliberate） | 保留，在「偏差登记」里写明理由 |
| **有害偏差**（Accidental） | 重构（需要变更计划 + 失败路径探针 + 真实应用回归） |
| **中性偏差** | 不处理，可记一笔 |

**判断"有害"的唯一可靠标准**：真实应用（Via 这类）是否变差。
**探针通过 ≠ 应用可用**（v141 的 Java IO hook 探针 W38 成功，却把 Via 打成完全不可交互）。

**为什么定这条**：本项目 `VirtualApplicationManager` 涨到 4540 行、`native-hook.cpp` 6547 行，
就是因为"文件变大后没人说得清它负责什么"。重构若不做镜像文档，只是把大文件拆成几个
没人认识的小文件，等于换一种方式失控。

**谁来做**：重构的同一个提交里完成，不允许"以后再补"。

### 规则 8：代码注释最小化（详细描述只在文档里）

> **代码里最少注释；只写「怎么用」；完整用法、实现说明、为什么这么写，全部放镜像文档。**

| 位置 | 写什么 | 上限 |
| --- | --- | --- |
| 类级 KDoc | 一句话职责 + 镜像文档路径 | **3 行** |
| 函数 KDoc | 一句话作用；有前提/副作用时补一句 | **2 行** |
| 行内注释 | 只在"不写会让人误解"的地方（非显然的反射、位运算、绕过手段） | 尽量不写 |
| ❌ 禁止写在代码里 | 背景故事、偏差登记表、"约束与坑"清单、历史沿革、AOSP 对照 | 0 |
| ✅ 这些放哪 | `docs/代码镜像/<同路径>.md` 的四节（职责/函数/偏差登记/约束与坑） | — |

**为什么**：
1. 注释和代码在同一文件，改动时**最容易失同步** —— 过时的长注释比没有注释更糟；
2. 本项目已因"文件里塞满说明"导致单文件膨胀（`VirtualApplicationManager` 4540 行）；
3. 镜像文档已经承载完整描述，**同一份内容不要写两遍**。

**自查**：一个文件里 KDoc 行数 > 代码行数的一半 → 说明该搬文档了。

### 规则 9：拆一次 = 补一次测试 + 补一次文档

> **每次拆分/新增文件，必须同时交付：样例测试 + 镜像文档。** 不允许"先拆，测试和文档以后补"。

| 拆分动作 | 必须同时产出 |
| --- | --- |
| 从大文件迁出一个职责 | ① 该文件的 `*Test.kt`（能纯 JVM 测的部分；确实不可测的要在镜像文档「约束与坑」里写明原因） |
| | ② `docs/代码镜像/<同路径>.md`（四节：职责/函数/偏差登记/约束与坑） |
| 加一个开关/分支 | ①②  同上，且测试要覆盖**两个分支** |
| 改可见性（`private`→`internal`） | ① `--deps` 检查结果记录进镜像文档的「约束与坑」 |

**CI 按包粒度执行**（`module-test.yml` 的 `test_filter` 输入）：

```powershell
# 只跑 activitythread 包（刚拆完的模块）
node tools/release/gh-api.mjs --dispatch module-test.yml main \
  modules=":core:virtualization" \
  test_filter="com.nextvm.core.virtualization.engine.activitythread.*"
```

**为什么按包**：整模块测试会连坐（一个包改坏、全模块红），按包跑能直接定位到刚拆的那部分，
且更快（只编译一个模块 + 只跑匹配类）。

### 规则 10：拆分必须证明「逻辑未变」

> **golden 只锁定当前行为，不能证明重构没改逻辑。** 搬运代码后必须对照 `_ref` 的原始实现。

```powershell
# 1) 导出重构前的文件（commit 必须用容器仓库的，不是主仓库的）
git -C _ref/ShitJumpingVM show <commit>:<path> | Set-Content tools/tmp/old.kt -Encoding UTF8

# 2) 对比函数体逻辑（归一化：去注释/字符串内容/空白）
node tools/lint/check-refactor-equivalence.mjs `
  --old-file tools/tmp/old.kt --old-fn <旧函数名> `
  --new-file <当前文件> --new-fn <新函数名>
```

| 结果 | 要求 |
| --- | --- |
| **等价 ✓** | 纯搬迁，通过 |
| **不等价 ✗** | 必须在镜像文档「偏差登记」里解释**语义为何等价**；解释不通就是重构引入的行为变化，要回退 |
| 逻辑确实应当改变 | 走 `docs/输出/变更/<date>-<task>.md` 变更流程，**不能藏在重构里** |

**已知的结构性改造（允许不等价）**：把"直接读某类的私有字段"改成"构造时回调注入"，
如 `translatePath` 的 `hiddenPaths.contains(x)` → `isHidden(x)`。

**为什么需要这条**：本轮 golden 是按重构后的实现推导的 —— 如果抽取时逻辑被改了，
golden 会一起错，测试照样绿。静态等价检查是**唯一能证伪"逻辑没动"的手段**。

### 每类目录回答什么问题

| 目录 | 回答 | 什么时候读 |
| --- | --- | --- |
| `计划/` | 接下来做什么 | 定任务、看优先级 |
| `架构/` | 它怎么运作 | **改代码前必读** |
| `设计/` | 为什么这么选 | 要推翻/延续设计决策时 |
| `现状/` | 现在什么样 | 判断某能力是否真生效 |
| `输出/` | 做过什么、证据在哪 | 复盘、找实测数据 |
| `archive/` | 哪些结论是错的 | **引用结论前对照** |

## 大量精确修改前必须落盘修改计划

> **触发条件（满足任一即算"大量"）**：改动涉及 ≥3 个文件 / ≥50 行 /
> ≥2 个 Gradle 模块 / 删除或重命名文件 / 触碰 hook、路径重定向、权限判断。

**这类改动一律走两步，不许直接开改：**

### 第 1 步：落盘修改计划（改之前）

文件：`docs/输出/变更/<YYYYMMDD>-<任务名>.md`

必须包含：

| 章节 | 内容 |
| --- | --- |
| **目标** | 一句话 |
| **影响范围** | 文件清单 + 每个文件的改动类型（新增/修改/删除） |
| **逐文件 diff 预览** | 改前/改后片段；**跨文件或有歧义时用 `diff -u` 格式** |
| **风险** | 回归点、可能破坏的既有行为 |
| **回退方案** | 具体到命令（`git revert <sha>` / 恢复某个文件） |
| **验证方法** | 可执行命令 + 判据 |

**生成方式（命令行，不手写猜测）**：

```powershell
# 方案 A：先改到工作区，用 git 生成真实 diff，再决定是否提交
git -C _ref/ShitJumpingVM diff > docs/输出/变更/<date>-<task>.diff
git -C _ref/ShitJumpingVM diff --stat

# 方案 B：不确定时，先把"打算改什么"写成 patch 文本，用 --dry-run 校验
git -C _ref/ShitJumpingVM apply --check my.patch
```

**落盘后停下来等确认**：`docs/输出/变更/<...>.md` 写完即报告"计划已就绪，请查看"，
**不要在同一条回复里继续改代码**（除非明确说"直接改"）。

### 第 2 步：改完后追加实际 diff（改之后）

在同一文件的 `## 实际变更` 章节追加：

```powershell
git -C _ref/ShitJumpingVM diff > docs/输出/变更/<date>-<task>.actual.diff
git -C _ref/ShitJumpingVM diff --stat >> docs/输出/变更/<date>-<task>.actual.diff
```

**`.actual.diff` 与计划中的"预期"不一致时，必须在文档里解释差异原因**。

### 为什么定这条约束

历史上已经发生过几次"边想边改、越改越偏"：
`DiagnosticsSink` 半成品、WebView hook 先加后回退、`CACHE_EPOCH` 基于错误诊断清理缓存。
**落盘 + 等确认** 是最便宜的纠错点。

## CI 触发：**只能手动 dispatch，一次一个，明确指定**

**规则（2026-11-24 定；`push` 自动触发已全部移除）**：

> **两个编译 workflow 都只有 `workflow_dispatch`，没有 `push`。**
> **要编译，必须明确指定是 `probe-build.yml` 还是 `container-build.yml`，一次一条命令。**
>
> 用户的原话：「要触发编译的 CI 只能触发一次编译，明确指定是容器或者探针编译。」

### 为什么去掉自动触发

`push` 的 `paths` 是按「变更集里有没有这些文件」判断的。一次 push 同时改了
`probe/**` 和 `.github/workflows/container-build.yml`，**两个 workflow 都会跑** ——
这不是配置写错，是「自动触发」这个机制本身保证不了「只跑一个」。

2026-11-24 实际发生过（我一次 push 同时含 workflow 文件和 probe 源码）：

```
45386771719  build-probe      push   2852d50   <- 预期
45386771274  build-container  push   2852d50   <- 不该跑
```

详见 `docs/输出/变更/20261124-ci-manual-dispatch-only.md`。

### 正确的触发方式

| 场景 | 命令（一次一条） |
| --- | --- |
| **改了探针**（`probe/**` 等） | `node tools/release/gh-api.mjs --dispatch probe-build.yml main` |
| **改了容器源码**（`wentureing/ShitJumpingVM`） | `node tools/release/gh-api.mjs --dispatch container-build.yml main` |
| **改了文档/工具/workflow** | 什么都不跑 |

**`push` 不再触发任何编译** —— 所以 push 文档、push workflow 文件都是安全的。

### 违反判据（自查）

- 同一个 `head_sha` 出现**两个** run
- 同一个 workflow 出现**两个** run

以上任一 → 就是违反本规则。触发后**不要**为了「保险」再 dispatch 第二次。

### 历史教训（为什么现在这么定）

**教训 1：push 后又 dispatch**

```
#29  build-probe      cancelled  push               <- push 自动触发
#30  build-probe      cancelled  workflow_dispatch  <- 我又补一次
#31  build-probe      cancelled  workflow_dispatch  <- 又补一次
```

三个 run 撞在同一个 `concurrency` group（`cancel-in-progress: true`）→ **全部互相取消**，
一个可用产物都没有。

**教训 2：靠 `paths` 过滤保证「只跑一个」是不可靠的**

我一度以为 `paths` 过滤能保证「改探针只跑探针」，实际它只看变更集里**有没有**那些文件。
只要一次提交同时碰到两边，两个都会跑（见上）。

**→ 所以现在改成：编译只能显式 dispatch。**

### 为什么容器一直都要手动 dispatch

容器源码在**独立仓库** `wentureing/ShitJumpingVM`，
主仓库的 `container-build.yml` 是通过 `actions/checkout` 从那个仓库拉代码的，
所以**主仓库 push 与容器代码变化无关**，从来就无法自动感知。

### 排查 CI "看起来像故障" 的第一件事

看 `event` 字段：`push` 还是 `workflow_dispatch`。
**现在不应该再出现 `push` 的编译 run** —— 出现就是配置被改回去了。

---

## CI 触发后不要阻塞等待

**触发构建后直接继续下一步工作，不要轮询/sleep 等结果。**

需要结果时再单独查一次：

`powershell
node tools/release/gh-api.mjs "/repos/wentureing/sandboxsensor/actions/runs?per_page=3"
# 看 status / conclusion / head_sha
`

**理由**：容器构建 3-5 分钟、探针 2-3 分钟，阻塞等待会白占一整轮对话。
正确节奏是「改代码 -> 触发 CI -> 同一轮内做下一个任务的计划/编码 -> 稍后再查一次」。

例外：只有下一步动作**必须以构建产物为前提**（例如要装包验证）时，才单独查一次；
若仍 in_progress 就留到下一轮再查，不要死等。

## 构建变体与签名

- **CI 构建的是 `:app:assembleDebug`**（`container-build.yml` 的默认 `task` 与执行行）
  - 设备上的包：`pkgFlags=[ DEBUGGABLE HAS_CODE ... ]`，`run-as` 可用
  - **`LOGD` 一直可见** —— 排查主力日志级别
- **`LOGD` 保留，不改 `LOGI`**：release 里没有是预期行为（避免影响 release 效率）。
  **约定：排查固定用 debug 包**；将来若引入 release 分发，排查时必须换回 debug 包。
- **签名必须固定**：workflow 的 `Cache debug keystore`（key `android-debug-keystore-v1`）
  - 不固定 → `install -r` 报 `INSTALL_FAILED_UPDATE_INCOMPATIBLE` → 必须卸载重装
  - **卸载会清掉两样**：`appops` 授权（如 `MANAGE_EXTERNAL_STORAGE`）**和**沙箱数据（所有 guest）
  - 所以固定签名不只是"省一次点击"，是保住授权状态和数据
- **版本号递增**：`versionCode = GITHUB_RUN_NUMBER`、`versionName = 1.0.0-<7位sha>`
  递增的 versionCode 配合固定 keystore 才能 `adb install -r` 覆盖
- **现阶段不做 release 分发包**（`app/build.gradle.kts` 没有 `signingConfigs`，也用不到）
- **⚠️ 签名固定的验证方法（2026-11-24 发现这个问题从未验证过）**
  - 连续两次 CI 的 APK 必须用 `apksigner verify --print-certs` 对比证书 SHA-256，**完全一致**才算成功
  - 本项目的 `Cache debug keystore` **一直没生效**：Post 步骤报
    `[warning]Path Validation Error: Path(s) ... do(es) not exist`
    → 每次构建都生成新 keystore → 每次签名不同 → `adb install -r` 必失败
  - 详见 `docs/输出/记录/记录-签名漂移根因-Cache-keystore从未生效.md`

## 提交前必跑：本地静态检查（毫秒级）

```powershell
# 常规检查（括号平衡 / KDoc 内危险 /* / 注释未闭合）
node tools/lint/kotlin-lint.mjs <改动的目录或文件>

# 拆分/迁移代码后**必跑**：源文件的 private 成员有没有被新文件引用
node tools/lint/kotlin-lint.mjs --deps <源文件> <引用它的新文件>

# 移包/迁移后**必跑**：同模块跨包引用是否漏了 import（要传整个模块 src 目录）
node tools/lint/check-imports.mjs <模块 src 目录>

# 新增/修改 hook 入口时（提示模式，不阻塞）
node tools/lint/check-hook-idempotent.mjs <目录>
```

### 为什么需要 `check-imports`（真实教训）

本项目连续两次因"移包/迁移后漏 import"让 CI 失败：

| 次 | 场景 | 缺失 | 耗时 |
| --- | --- | --- | --- |
| 1 | 迁移 `CustomerProbes` | `import com.nextvm.core.common.findField` | 1 轮 CI（2 分钟） |
| 2 | 移包 `LegacyMessageHandlers` | `import ...engine.ActivityThreadHook` | 1 轮 CI（2 分钟） |

`check-imports.mjs` 把这类错误提前到**毫秒级**：建立"类型名 → 包"表，
找出"引用了别包类型但既没 import、也不在嵌套访问里"的地方。

已知误报：嵌套类访问（`Outer.Inner`）、全限定写法 —— 已在规则里排除，仍有少量需人工判断。

退出码 0 = 通过。**两个命令都在毫秒级**，不需要 Gradle / Android SDK。

### 为什么自写，而不是用 detekt / ktlint

联网核对过：**detekt 的类型解析不完整**（有实测报告：377 个编译器错误静默通过），
ktlint / semgrep 同理 —— 它们**都不做完整类型解析，查不出「未定义符号 / 私有成员越界」**。
那类错误只有编译器能查，而编译器需要 Gradle + Android SDK（分钟级）。

所以分工是：

| 层 | 工具 | 耗时 | 能拦什么 |
| --- | --- | --- | --- |
| **① 本地自写检查** | `tools/lint/kotlin-lint.mjs` | **毫秒** | 括号不平衡、KDoc 内 `/*`（Kotlin 块注释可嵌套 → Unclosed comment）、注释未闭合、**跨文件访问 private 成员** |
| ② 编译器 | `module-test.yml`（只编译被测模块） | ~2 分钟 | 未定义符号、类型错误、可见性 |
| ③ 完整构建 | `container-build.yml` | ~4 分钟 | 全部 + 打包 |

**① 的存在意义**：把"本来要等 2 分钟 CI 才知道"的错，提前到提交前几毫秒发现。
本项目已经连续 3 轮因为同一类问题（拆分后漏私有依赖 / KDoc 里的 `/*`）让 CI 失败。

### 教训（真实案例）

| 症状 | 根因 | ①能否拦 |
| --- | --- | --- |
| `ProcSpoofing.kt:151:1 Unclosed comment` | KDoc 里写了 `` `/proc/self/*` `` | ✅ 能（报出根因行并提示这是嵌套注释） |
| `CustomerProbes.kt:473 Cannot access 'loginProbeLastSig': it is private` | 迁移代码时漏了改可见性 | ✅ 能（`--deps` 直接列出） |
| `CustomerProbes.kt:303 Unresolved reference 'findField'` | 漏 import | ⚠️ 部分（只能靠编译器；①不查 import 完整性） |

## 其他约定

- **Kotlin KDoc 里禁止出现 /****：Kotlin 块注释**可嵌套**。KDoc 里写路径通配
  <sandboxDataRoot>/<instanceId>/** 会打开一个新的嵌套注释，导致 CI 报
  Unclosed comment（CI #25 已踩）。通配一律改写成中文描述「.../ 下的任意路径」。
  提交前可扫一遍注释区的 /**。
- **凭据**：`token.txt` 不入库、不读取内容到对话中；脚本内读取后立即用于请求
- **产物命名**：`<类型>-<7位sha>[-<abi>].apk`
- **容器 command 通道**：`adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.{INSTALL|LAUNCH|LAUNCH_ACTIVITY|UNINSTALL|LIST|COLLECT_OUT|LIST_OUT|CAT_OUT}`
  - `LAUNCH_ACTIVITY --es pkg <pkg> --es activity <完整类名>`：容器启动 guest 时**只解析 LAUNCHER**，要起非 LAUNCHER 页面必须用这条
  - 结果统一打到 `logcat -d -s CmdReceiver`
  - **导入通道（2026-11-23 22:30 更正）**
    - **`/sdcard/Download/` 可用，前提是 `MANAGE_EXTERNAL_STORAGE = allow`**
      实测：`default` 时 `INSTALL` 报 `APK file not found`；`allow` 后**同一个包直接 `Success`**
      （p26 容器、不含 p28 的 access 豁免也能成功 → **权限才是关键，不是豁免**）
    - `/data/local/tmp/` 也可用（644，无需额外授权）
    - **不要用 `run-as` 验证应用权限**：`run-as` 跑在 `runas_app` SELinux 域，
      与应用的 `untrusted_app` 域不同，它的 `Permission denied` **不代表**应用读不到
    - 详见 `docs/输出/记录/记录-导入通道真实约束.md`（含更正说明）
    - 用法：`adb push x.apk /sdcard/Download/` 或 `/data/local/tmp/`
      然后 `INSTALL --es path <path>`
  - 其他路径仍需放到 app 私有目录（容器自身把 `/sdcard` 重定向到沙箱）
- **验证应用能否读某路径**：必须让**应用进程自己**发起调用看结果，
  **不能用 `run-as`、也不能只从 `ls -l` 推断**（`run-as` 是 `runas_app` 域；
  `ls -l` 只反映 DAC，真实访问还经过 SELinux 与 FUSE/appops）
- **测试设备**：`10AFA8169Z0039G`（vivo V2463A / Android 15 / arm64-v8a / PAGESIZE=4096）
- **设备时钟**比电脑快约 2 分钟，日志按设备时间切片

## Kotlin DSL（build.gradle.kts）里禁用 `java.util` / `java.io` 直接写法

> **`java` 在 Gradle Kotlin DSL 里会被优先解析成 `JavaPluginExtension`**（即 `java { }` 块），
> 所以 `java.util.Properties()` / `java.io.File(...)` 会报 `Unresolved reference: util / io`。

必须在脚本**顶部** import 并起别名：

```kotlin
import java.io.File as JavaFile
import java.util.Properties as JavaProperties
```

然后 `JavaProperties()` / `JavaFile(path)`。

**已踩三次**：CI 编号 22（probe 模块）、以及 `upstream-baseline` 分支的签名配置两次。
排查时先看 `build.gradle.kts` 顶部有没有注释 —— fork 的 app 模块里已经写了这个坑。

---

# 2026-11-25 新增约定（B 阶段：容器代码优化 + 测试）

> 这些是**用户在本轮明确要求**的工作方式，与上面规则冲突时以本节为准。

## 规则 11：读写文件一律用工具，不用 shell 拼接

| 操作 | 用什么 |
| --- | --- |
| 读文件 | `read`（**不要** `Get-Content` / `cat` 看内容） |
| 新建/整体重写 | `write` |
| 局部修改 | `edit`（先 `read` 再改） |
| 搜索 | `grep` / `glob`（**不要** `Select-String` 找内容） |
| `pwsh` | 只用于 git / adb / 构建 / 只读校验脚本 |

**例外**：只读的校验脚本（`check-*.mjs`）可以用 `node` 跑；批量机械移动/删行可以用脚本，但**内容写入仍走 `write`/`edit`**。

## 规则 12：不猜测上下文，不节省必要的代码阅读

- 写代码前必须**实际读到**相关声明（变量类型、函数签名、调用点），不能凭 grep 行号推断；
- 改动 300+ 行或跨 6+ 函数时，**完整读**每个函数再动手（用户明确："对话有空余，不节省"）；
- 看到可疑行为时先读源码/日志验证，再下结论。

## 规则 13：每批拆分 = 两个职责 + 三件套

| 项 | 要求 |
| --- | --- |
| 粒度 | **一批最多两个职责**（用户 2026-11-25 定）；超出就分批 |
| 三件套 | ① 代码（纯逻辑抽出可测）② `*Test.kt`（纯 JVM）③ 镜像文档（四节） |
| 命名 | 按**实际行为**命名（例：`AutofillProbes` → `AutofillSuppressor`，因为它改状态，不是只读探针） |
| 边界 | 只搬实现、不搬触发；上下文靠参数；日志格式一字不改 |
| 验收 | `kotlin-lint` + `check-imports` + `check-test-fn-names` → CI matrix → 真机 3 项 |

## 规则 14：diag 包结构（诊断/探针的唯一去处）

```
core/virtualization/diag/
  ContainerSelfCheck.kt          主动自检（CmdReceiver SELF_CHECK）
  Reflect.kt                     fieldOf（沿父类查找，唯一实现）
  dump/GuestMemoryDump.kt        线程栈 / 内存 dex
  dump/MapsSnapshot.kt           maps 快照
  trace/InitTrace.kt             方法追踪启停 + 过滤
  probe/PluginProbes.kt          只读观测
  probe/HijackProbes.kt          只读观测
  probe/LoginProbes.kt           登录/认证观测 + 点击包装
  suppress/AutofillSuppressor.kt 行为抑制（不是探针）
```

- **`VirtualApplicationManager` 里不再放探针**（4589 → 2337 行）；
- 自检/探针**不许写 `virtualRoot`**（用户可见目录）——写 app 私有 `cacheDir`；
- 容器自检与 `probe/`（独立 APK）是两套东西：自检测"容器动作是否可行"，探针测"guest 视角是否一致"。

## 规则 15：CI 与测试的操作口径（2026-11-25 更新）

| 场景 | 做法 |
| --- | --- |
| 触发编译 | 仍是一次一个、手动 dispatch（容器 `container-build.yml` / 探针 `probe-build.yml`） |
| 旧 run 落后于新代码 | `node tools/release/gh-api.mjs --cancel <run-id>` 再 dispatch（用户允许） |
| 单元测试 | `module-test` 已是**模块级 matrix**；可用 `test_filter` 精确到包 |
| 测试卡死 | 日志有 `TESTSUITE/TESTSTART/TESTTIME` 打点；**有 TESTSTART 无 TESTTIME = 卡在该测试** |
| 单元测试失败 | 不严重（依赖限流/测试数据）→ 修测试即可；**容器发包已前置**，产物不受影响 |
| 提交前 | `kotlin-lint` / `check-imports` / `check-test-fn-names`（毫秒级） |

## 规则 16：能力清单与回归

- `docs/现状/容器能力清单与回归方法.md` 是"能力不能丢"的可执行版本（14 项，含验证命令）；
- **每批拆分后至少跑 5 项**（探针 diag / stackdump / 宿主写入 / 豁免名单 / breadcrumb 尾部）；
- **全量回归留到重构最后阶段**（用户决定）。

## 规则 17：`tools/tmp/` 不入库

`tools/tmp/` 已加入 `.gitignore`（含 AOSP 浅克隆副本与临时脚本）。提交时**不要** `git add -A`，用明确路径。
