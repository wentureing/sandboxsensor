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
