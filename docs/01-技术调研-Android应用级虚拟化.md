# Android「应用级虚拟化」开源项目技术调研（v2）

- 调研日期：2026-11-21（第二次修订：剔除 VirtualApp 系；补充 NestSpace 尽调）
- 目标：把**已安装的 App 或未安装的 APK** 导入宿主应用内部**直接执行**，不需要虚拟机 / Root / 系统改机。三个技术重点：
  1. **地址（路径 / 内存映射）的重新映射**
  2. **不被寄主（被运行的 guest App）发现自己在容器里**
  3. **进程间资源管理**
- 目标系统基线见 [`02-AOSP-目标系统基线.md`](./02-AOSP-目标系统基线.md)

---

## 决策摘要（TL;DR，2026-11-21 定稿建议）

| 决策项 | **明确建议** | 依据 |
| --- | --- | --- |
| 技术路线 | 只在宿主 ART 上做**应用级虚拟化**；**不做虚拟机、不做自研 DEX 运行时** | `chuccp/android-vm` 用 8.3 万行 C++ 只换来 target-app 级首帧（3.5 节） |
| 路径形态 | **默认 B1：虚拟标准路径 `/data/user/0/<guest>`**，方案 A（真路径）保留为可切开关 | 硬编码 `/data/data/<pkg>` 的 SDK 存量太大，A/B2 救不了（`03` §2.1） |
| 路径实现 | Context 层统一用 **`root + 相对路径` resolver**；禁止散落硬编码沙箱路径 | 换 root 即完成 A/B 切换，否则要改十几处（`05` §6） |
| 承接时机 | **W1**（`bindApplication` 最早期注册 native 环境）+ **W2**（`attachBaseContext` 之后、**`installContentProviders` 之前**替换 Context） | **ContentProvider.onCreate 早于 Application.onCreate**，等到 `onCreate` 就漏了（`07` §2） |
| 产品姿态 | **架构按「支持姿态」设计**（resolver + 规则表 + native 层可关），**运行默认 B1** | 兼顾覆盖面与"零 native 层"降级退路（`07` §7、§9） |
| native 拦截 | **只 patch 目标模块的 GOT/PLT，绝不 patch 进程 libc** | 宿主 IO 不受影响 ⇒ 无需 Host/Target 窗口，可逐库回滚（`03` §4.3） |
| 符号集 | P0：12 个读/stat 符号 + **fd→虚拟路径表**；P1：写与目录操作；**P2 `/proc/self/maps` 先不做** | `06` §3 |
| 未知路径 | **DIAG + PASS**，并作为首要诊断指标；其他包/越界一律 **ENOENT**（不用 EACCES） | `06` §4.3、§7 |
| 启动链介入 | **替换 `ActivityThread.mInstrumentation` 为主**，注入 `LoadedApk.mClassLoader` 为辅；`EXECUTE_TRANSACTION(159)` 仅作补充 | 一个总闸覆盖全生命周期且语义稳定；159 是硬编码常量更脆（`04` §2） |
| 组件拦截 | 改成 **规则表 + Matcher + 桩响应**（借 Espresso-Intents 结构），不用 if-else 链 | 可按 App 灰度、可单测、可诊断（`05` §2） |
| 异步等待 | **IdlingCounter 替代固定 sleep** | 直接决定 AI/MCP 自动化的稳定性（`05` §3） |
| 运行时权限 | 宿主 manifest **白名单预声明** + 容器代答 permission-controller Intent | 权限控制器只认已安装包（`04` §4） |
| 系统版本 | **Android 14/15/16（API 34/35/36）主支持**，33 降级，**不做 17** | 14+15+16 = 56.4% 活跃设备；17 仅 1%（`02` §1） |
| 代码来源 | **NestSpace（Apache-2.0）摘取实现**，不整仓演进；**不引入任何 VA 系** | 无 VA 派生痕迹、无 GPL 依赖；但 19 天 88k 行属 AI 高速产出（3.3 节） |
| 明确不做 | FUSE / bind mount / chroot（需 root）、proot ptrace（10–100× 慢）、内联 `svc` 拦截、ART 方法 hook（非必要不引入） | `03` §3、`06` §3 |

> 完整推理链见各专章；本表是决策记录，后续如有变更请在此更新并注明日期。

---

## 0. 排除清单（硬约束，记入项目约定）

> **本项目不引入、不参考、不 fork 任何 VirtualApp / VApp / 虚拟大师 / VA 系代码或其衍生仓库。**
>
> 原因：该商业公司被指对开源社区（含独立开发者与派生项目的作者）存在无依据的法律威胁与骚扰式主张，供应链与合规风险不可控。为避免后续无意引入，列明排除范围：

| 排除对象 | 类型 | 说明 |
| --- | --- | --- |
| `asLody/VirtualApp` | VA 上游 | 原始开源仓库 |
| `FBlackBox/BlackBox` | VA 衍生 | README/注释自述基于 VirtualApp |
| `FSpaceCore/SpaceCore` | VA 衍生 | topics 含 `virtualapp`，同族架构 |
| `ServenScorpion/VirtualApp` | VA 衍生 | 直接 fork |
| 任何仓库名含 `VirtualApp` / `VA-Ex` / `VaEx` 的项目 | VA 衍生 | — |
| `Pangu-Immortal/LSPlantPro` | **VA 适配版**发行物 | 描述为 "VA-adapted" 预编译库，与本项目无关，不使用 |
| 任何以"VA 适配"为卖点的中间件（hook 库、崩溃保护、加固兼容层） | 生态周边 | 需逐个确认不含 VA 代码 |

**判定与操作规则（写进 review checklist）：**

1. 引入任何第三方仓库前，先 `grep -ri "virtualapp\|com\.lody\|VActivityManager\|VPackageManager\|va-ex"` 全仓搜索，命中即拒绝（除非命中项是自研同名数据类，见下）。
2. 不引用"基于 VirtualApp"的博客/教程作为实现依据；本领域公共原理（占位组件、libc 重定向、ClassLoader 加载）是 Android 通用知识，可按 AOSP 文档独立实现。
3. 商业公司产品（虚拟大师、双开助手等）**仅作黑盒对照测试对象**，不做逆向、不抄代码。
4. 术语上不再用"VA 系"指代本技术方向，统一称「应用级虚拟化容器」。

> 注：v1 文档曾把 VA 系当作基线对比，已按本节要求全部剔除并重写。

---

## 1. 技术定性：先明确它「不是」什么

| 方案 | 隔离层级 | 是否满足本项目要求 |
| --- | --- | --- |
| **AVF（Android Virtualization Framework，pKVM）** | 硬件虚拟化 VM | ❌ 就是虚拟机 |
| **云手机 / 整机镜像** | 整机 | ❌ 重、需系统镜像 |
| **Work Profile / 厂商应用分身（多用户）** | 系统管理的独立用户 | ❌ 需 Device Owner / 厂商能力，不能导入任意 APK |
| **LSPatch 类重打包注入** | 同一个真实安装的 App | ⚠️ 改变包体与签名，无法"内部执行未安装 APK" |
| **应用级虚拟化容器（本项目方向）** | 宿主进程 + 多 stub 进程 | ✅ |

**通用原理**（依据 [HackTricks](https://hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/android-application-level-virtualization.html) 与 [Zahid's Blog](https://blog.azzahid.com/posts/android-app-virtualization/)，均为对 Android 公开机制的描述）：

1. 宿主是正常安装的真实 App（真实包名、UID、权限）；guest **运行在宿主 UID 之下**。
2. 用自定义 `ClassLoader` + 构造框架内部 `LoadedApk`，把 guest 的 `base.apk` / split / so / resources 加载进宿主进程（`openDexFileNative`、`AssetManager.addAssetPath`）。
3. Java 层代理/Hook 劫持 `IActivityManager`、`IPackageManager` 等系统服务接口。
4. native 层 patch libc 导入符号（`open/openat/stat/access/readlink`…），把 guest 的路径读写重定向到宿主私有目录下的虚拟布局。
5. manifest 预注册占位组件（Stub Activity/Service/Provider/Receiver）+ 多进程槽位承载 guest 的四大组件。

> 安全含义：宿主 UID 共享 ⇒ guest 继承宿主全部已授予权限 ⇒ 容器**不是安全沙箱**，也是风控识别的主要入口。

---

## 2. 可选项目清单（已剔除 VA 系）

| 项目 | 活跃度（核实日 2026-11-21） | 许可 | 技术路线 / 关键能力 | 匹配度 |
| --- | --- | --- | --- | --- |
| [chuccp/NestSpace](https://github.com/chuccp/NestSpace) | 极活跃：创建 2026-11-03，最新提交 2026-11-21，7 个 Release（v1.0.1→v1.0.7，11-05 至 11-11） | Apache-2.0 | 应用级容器；**导入已安装 App + 导入 APK（base+split）**；`:c1`–`:c16` 进程槽位池 + 1–16 可配置 + LRU 回收；`VirtualEnvironment` 统一事实源；native 路径 / `/proc` / property facade；Java 插件平台 | ★★★★★（详见第 3 节尽调） |
| [woand/ShitJumpingVM](https://github.com/woand/ShitJumpingVM) | 活跃（alpha，NEXTVM fork，非 VA 系） | Apache-2.0 | 10 个 stub 进程槽 `P0–P9`、160 个 manifest 占位组件；AM/PM 代理 + `EXECUTE_TRANSACTION` 拦截；自写 GOT/PLT hook；沙箱布局 `files/virtual/{apks,data}` | ★★★★☆ 结构最干净，可读性最好 |
| [TanvirHossain2/NEXTVM](https://github.com/TanvirHossain2/NEXTVM) | **上游快照：仅 4 个提交，全部 2026-03-07，之后停更** | Apache-2.0 | 与 ShitJumpingVM 完全同一套模块划分（`core/{virtualization,binder,sandbox,hook,apk,framework,services,model,common,designsystem}`）；Status=Alpha，targetSdk 35 | ★★☆☆☆ 只作为 ShitJumpingVM 的原始快照读；**活跃开发在 fork 上，不在本仓库** |
| [obadadallo95/mirro-android-virtualization](https://github.com/obadadallo95/mirro-android-virtualization) | 原型（2026-09） | Apache-2.0 | 研究型：Binder / 系统服务边界、动态加载器、classloader 与 profile 执行 | ★★★☆☆ 读设计取舍 |
| [LSPosed/LSPatch](https://github.com/LSPosed/LSPatch) | 活跃 | — | 无根 Xposed：**重打包注入**（非容器） | ☆ 相邻路线，参考注入手法 |
| ~~asLody/VirtualApp~~、~~BlackBox~~、~~SpaceCore~~、~~ServenScorpion/VirtualApp~~、~~LSPlantPro~~ | — | — | **已按第 0 节排除** | ✗ |

> 结论：真正"免 VA 生态"且工程完整度可用的参考只有 **NestSpace** 和 **ShitJumpingVM** 两个，且都以 Apache-2.0 发布。

---

## 3. NestSpace 尽调（许可证 / 进度 / 可用性）

核查方式：本地浅克隆 `_ref/NestSpace`（`git clone --depth 50`）+ GitHub API + LICENSE 全文比对。

### 3.1 许可证：Apache-2.0，标准全文，适合改造

| 检查项 | 结果 |
| --- | --- |
| `LICENSE` 文件 | **标准 Apache License 2.0 全文，202 行，无附加条款、无商业限制、无署名强制 UI** |
| `NOTICE` 文件 | 不存在（改造后无额外 NOTICE 合并义务，但仍须保留 LICENSE 与版权声明） |
| GitHub 标注 | `license.key = apache-2.0` |
| 是否含 VA 派生 | **仓库内 0 命中** `com.lody` / `VActivityManager` / `VPackageManager`；`VirtualApp` 仅出现在自研数据类 `com.coke.nestspace.data.VirtualApp`（表示"虚拟应用"实体，与 VA 项目无关） |
| 第三方 hook 框架 | **0 命中** Dobby / xHook / ShadowHook / bhook / LSPlant / Xposed / epic；native 部分为自研（`app/src/main/cpp/core`、`cpp/hooks/libc`、`cpp/hooks/elf`，共 7 个 C/C++ 文件） |
| 主要依赖 | AndroidX / Compose / OkHttp / `vosk-android`（离线语音）/ `hiddenapibypass`（HiddenApiBypass，Apache-2.0） |

**结论：Apache-2.0 明确允许商用、闭源分发、修改与再许可。本项目可以把它当代码素材或起点，闭源改造合法。** 需要履行的义务只有：

1. 保留 `LICENSE` 副本与版权声明（可放在"开源许可"页面）；
2. 修改过的文件加显著"已修改"标注（Apache-2.0 §4(b)）；
3. 不得使用其商标/项目名暗示背书（§6）—— 建议改包名、改应用名；
4. 注意 §3 专利授权与 §5 贡献条款；若触发专利诉讼，授权自动终止。

**风险提示（与许可证无关，但更重要）：**

- 项目**创建于 2026-11-03**，19 天内产出 7 个 Release、约 8.8 万行 Kotlin/Java + 7 个 C++ 文件（统计口径：`app/src` 下 kt 375 / java 172 / cpp-h 7 个文件，行数≈88,251）。作者在 README 中自述大量使用 AI 编码（"可能是我的 AI 不行吧，消耗不少钱"）。**结论：代码体量大、迭代极快，但缺少外部验证与长期打磨。建议定位为「架构参考 + 可摘取的实现片段」，不要把整仓库当长期主干直接演进。**
- 社区面：8 star / 4 fork / **0 open issue**（说明尚无真实用户反馈回路）；无外部贡献者记录。
- 许可证合规只解决"能不能用"，不解决"用得稳不稳"。

### 3.2 开发进度（Release 时间线，可作能力成熟度证据）

| 版本 | 日期 | 与本项目相关的能力 |
| --- | --- | --- |
| v1.0.1 | 2026-11-05 | 初始容器 |
| v1.0.3 | 2026-11-05 | — |
| v1.0.4 | 2026-11-06 | — |
| v1.0.5 | 2026-11-06 | — |
| v1.0.6 | 2026-11-07 | **导入宿主已安装应用**；加固应用兼容修复；进程池子进程管理修复 |
| v1.0.7 | 2026-11-11 | **实例级存储 / AccountManager / Android ID / 权限 / WebView 目录 / 进程身份隔离**；多进程组件（显式 Activity、Service、Provider、广播、插件 APK 内组件）；native 路径 + `/proc` + property 兼容；**16 KB 页面大小设备兼容**；进程池 1–16 槽位 + 内存自适应 + LRU；槽位复用前结束旧进程避免同名 so / JNI 全局污染；崩溃只重启对应槽位；**验证范围：Android 15、16、17** |
| main 分支 | 2026-11-19 → 11-21 | `VirtualEnvironment` 基础层、Native 环境镜像、`AndroidStartupCoordinator`、声明式 runtime profile、native syscall identity gateway、proc 身份探针 |

> 判断：**在"我们最关心的三件事"上，NestSpace 是当前唯一有公开文档 + 可运行 APK + 有实测记录的实现**。但注意其验证范围是 **Android 15/16/17**，与本项目"至少 3 个活跃系统、不追新"的目标不完全一致——我们需要把 Android 14（15.3% 占比）拉进来，并放弃 Android 17（1%）。

### 3.3 改造可行性判断（要点）

| 维度 | 评估 |
| --- | --- |
| 许可证 | ✅ Apache-2.0，可闭源改造 |
| 架构可读性 | ✅ `docs/` 下有 15+ 篇设计文档，含能力边界、性能预算、Go/No-Go 清单 |
| 工程量 | ⚠️ 88k 行 Kotlin/Java，自研 UI（Compose）+ AI 助手 + 插件平台 + MCP，**与容器核心耦合较松的部分需要剥离**（AI/MCP/浏览器/桌面可整块删除） |
| native 能力上限 | ⚠️ 仅 GOT/PLT hook（只能拦导入符号），无 inline/ART 方法 hook。需要 ART 级 hook 时要自行引入框架，注意许可证（见下） |
| 签名 | ⚠️ Release 由作者正式签名（keystore 不公开）。**改造后必须自建签名 ⇒ 与原版无法覆盖共存，旧数据不可迁移**；Debug 与 Release 签名不同 |
| 文档可信度 | ⚠️ 文档与代码由同一 AI 辅助流程产出，存在"文档描述超前于实现"的可能（ShitJumpingVM 的作者在 README 中承认其上游 NEXTVM 的 README 是"愿望清单"）。**每条能力必须用自建探测 APK 实测，不采信文档** |

### 3.4 关于引入 ART hook 框架的许可证风险（重要）

若后续要补"Java 方法级 hook"能力，选框架时必须先看许可证：

- **LSPlant**（LSPosed）系 LGPL-3.0。Android 上以 `.so` 动态链接、且能替换库文件时可满足 LGPL 的"可替换"要求；但**以静态库（`.a`）形式链接进闭源 APK 存在明确合规争议**（第三方 VA 适配发行版正是以 prefab 静态库形式分发）。本项目**不采用**此类适配发行物。
- 更安全的候选是 Apache-2.0 / MIT 的 inline hook 框架（如 Dobby、ShadowHook、xHook 一类），仅在必要时引入，并做"可开关 + 可替换"设计。

### 3.5 边界案例：`chuccp/android-vm`（自研 DEX 运行时）实况调查

NestSpace README（2026-11-09）称这次尝试"依然没有成功：一个市面上的商业 App 都没法运行"。
**但 11-19 / 11-20 的提交表明它仍在推进，完成度远高于"失败"**（本地副本：`_ref/android-vm`）：

| 项 | 实测 |
| --- | --- |
| 定位 | 应用内嵌**自研 DEX 运行时** `libdexvm.so`：自写 DEX 解析、类链接、解释器、JNI、GC、Binder、协程调度；不编译 ART，`reference/art/` 仅作语义对照 |
| 规模 | **Java 1911 个文件 ≈ 24.8 万行** + **C/C++ 142 个文件 ≈ 8.3 万行** |
| 文档 | `docs/` 15 篇（基础 / 内存 GC / 类加载 / 协程 / JNI / native 库 / Binder / 桥接 / 启动流水线 / 关键算法 / 诊断 / API checklist 29KB） |
| 自述已验证 | 加固壳解密 → 动态加载 → Application/Provider/Activity 生命周期 → 首帧 present → **宿主真控件物化出画**（HWUI）→ 交互回灌闭环；均在其自建 `target-app` 上验证 |
| 自述未完成 | **"加固 APK 的业务页面"仍待逐屏回归**；Surface/GL、动画资源管线、输入法/无障碍接线待做；个别组件 measure 失败时"按现树降级 present" |
| 无 LICENSE | 仓库**没有 LICENSE 文件**（不可直接复用） |

**最贵的地方不是解释器，而是语义长尾**（README 自述的真实修复清单）：

- `lib` 新增类莫名 `NoClassDefFound` → 因为 runtime-libs 只编译自身源码树，缺类时 `javac` 失败且**静默跳过**
- `findViewById` 强转失败 → 必须按 AOSP 语义补 `LayoutInflater.Factory2` 询问链
- `ViewPager2` 构造/挂载反复失败 → 必须维护 `child.mParent` 不变量，并补齐 `AccessibilityManager`/`Scroller`/`Interpolator` 等缺类
- 连网络能力都要自己接（提交信息里出现 "OkHttp 百度源码抓取"）

**两条结论（都直接影响本项目决策）：**

1. 它不是失败，而是**用 8.3 万行 C++ + 24.8 万行 Java 换来了"target-app 级首帧 + 基础 UI"**；商业加固 App 的业务链路仍未跑通。
2. 自研运行时的成本是**无终点地补 framework/lib 语义**（缺一个类补一类，且缺类是静默的）。⇒ **本项目明确不走自研运行时/DEX 解释器路线**，只在宿主 ART 上做应用级虚拟化。

---

## 4. 同作者（chuccp）其他项目

| 仓库 | 语言 / 许可 | 时间 | 成效评估 |
| --- | --- | --- | --- |
| [chuccp/NestSpace](https://github.com/chuccp/NestSpace) | Kotlin / Apache-2.0 | 2026-11 | 主要项目；唯一与本需求直接相关的产出 |
| [chuccp/android-vm](https://github.com/chuccp/android-vm) | Java + C++ / **无 LICENSE** | 2026-11-11 → 11-20（仍在提交） | 自研 DEX 运行时，**不是失败**：8.3 万行 C++ + 24.8 万行 Java，target-app 级首帧与 UI 物化桥已验证，商业加固 App 业务页未回归（详见第 3.5 节）。**结论：不走这条路线** |
| chuccp/go-web-frame、go-tts、ai-agent、go-ai-agent、rust-web-frame、async-batch-inference | Go / Rust / Python | 2025-12 ~ 2026-11 | 与本项目无关，但说明作者产出节奏极快、跨度大 ⇒ 佐证"AI 大规模生成"的工作方式 |
| chuccp/eggy、RegionLivePreview、win-pdf-edit | TS / C# | 2026-09 ~ 10 | 无关 |

> 对同作者项目的总体判断：**只有 NestSpace 值得投入时间**；`android-vm` 的价值是"证明自研解释器/ART 路线不可行"，可作为立项时的风险论据引用。

---

## 5. 路线选择：「强化代理模式」能否替代多层路径拦截？

> 本章回答一个具体构想：**既然 guest 跑在宿主进程里，为什么不用宿主做"完整文件系统代理"，让路径全部由宿主代理和欺骗，从而省掉多层路径拦截/篡改？**

### 5.1 先承认一个物理约束：假路径与真路径，只能选一个

Java 层的 `Context`/`File` 代理**只能改变"返回值"**，改变不了内核。所以：

- **返回"标准假路径"**（`/data/user/0/<guest>`）→ guest 一旦 open，内核只认真实路径 ⇒ **必须有人在中间把假前缀改回真目录** ⇒ 这一层（native hook）无论如何都存在。
- **返回"真实沙箱路径"** → 零映射，但路径不在标准位置。
- **"两全"不存在**：无 root 无法在 `/data/user/0/<guest>` 造目录（那是别的 UID 的私有目录，SELinux 直接拒）。

**所以你的构想有一半是成立、且非常关键的一半**：

> **"完全代理"的极致形态不是"加强欺骗"，而是"取消欺骗"——不给假路径，就没有需要被改回的东西，路径映射层自然归零。**

### 5.2 因此真正该砍的不是"层数"，而是"观察面"

NestSpace 的全量 facade 之所以复杂，不在于它 hook 了 libc，而在于它要同时维护 **6 个观察面**互相不矛盾：

| 观察面 | 方案 A 是否需要 | 方案 C（NestSpace） |
| --- | --- | --- |
| Java：`Context` / `ApplicationInfo` / `PackageManager` | 需要（唯一） | 需要 |
| libc 路径符号（open/openat/stat/access…） | **不需要** | 需要（15+ 符号） |
| 直接 `syscall()` / 内联 `svc` | 不需要 | 需要探针，且**永远覆盖不全** |
| `/proc/self/{maps,status,mountinfo,cmdline}` 合成 | 不需要 | 需要 |
| fd → 虚拟路径映射（dup/fcntl/close 同步） | **不需要** | 需要 |
| system property 伪造 | 不需要 | 需要 |
| `stat` 元数据（st_uid/st_dev/st_ino） | 不需要 | 需要 |

**每多一个观察面，就多一组"必须一致"的测试**。这是复杂度的真正来源，也是"代理模式"构想的正确落点：**不是少写 hook，而是少开观察面。**

### 5.3 三种方案对比（含工程量与代价）

| | **A：真路径 · 零 native hook** | **B：真路径 + 单前缀 libc 替换** | **C：全量 facade** |
| --- | --- | --- | --- |
| guest 看到的 dataDir | `/data/user/0/<host>/files/s/<32位hex>`（中性命名） | Java 见 `/data/user/0/<guest>`，libc 把该前缀改成沙箱根 | 同 B，且 `/proc`、property、fd、stat 全部伪造 |
| native hook | **0** | 4 个符号 | 15+ 符号 + syscall 探针 + `/proc` 合成 |
| 观察面数量 | 1 | 2 | 6+ |
| 一致性测试量 | 小 | 中 | 大 |
| 能过的检测 | 不检查 dataDir 前缀的 App（多数普通 App） | 绝大多数常规检测 | 除硬件 attestation / 特权蜂窝外全部 |
| 工程量（相对） | 1× | 1.5× | 4×+（NestSpace 为此写了 20 篇设计文档） |
| 主要风险 | 路径带宿主包名 | 直连 syscall 绕过 | hook 崩溃 + 一致性维护无底洞 |
| **建议** | **起点** | **按需升级** | **只在被实测打败时才补单项** |

> **补充（2026-11-21 实测修正）**：方案 A 不是纸面假设——NEXTVM/ShitJumpingVM 的 `VirtualContext` 就是**直接返回真实沙箱路径**（`getFilesDir/getDataDir/getDatabasePath/nativeLibraryDir/sourceDir` 全部指向沙箱，只有 `getPackageName()` 返回 guest 包名），即它们实走方案 A。详见 `03-标准路径与沙箱文件系统.md` 第 4.2 节。

### 5.4 代理模式的四个隐藏陷阱（如果没处理好，会比 native hook 更难调）

1. **`File` 是值对象，代理只能改"入口"**。`new File(context.getFilesDir(), "x")` 得到的字符串会被 guest 缓存进 SharedPreferences、写进数据库、拼进 URL、上报服务端、做字符串比较。**你改得了返回，改不了"被记住之后的使用"**。一旦某个 SDK 在启动早期缓存了路径，后期再换值就产生"自己写的文件找不到"的灵异 bug。
2. **`getCanonicalPath()` 与 `getAbsolutePath()` 必须同时成立**。为了缩短路径而在沙箱根上做 symlink，会是灾难：`getCanonicalPath()` 会解析出真实路径直接暴露。**沙箱根必须是真实目录，禁止 symlink。**
3. **线程模型**。代理对象必须在任意线程可用（Binder 回调线程、`HandlerThread`、native 线程池）。`ThreadLocal` 代理会失效 ⇒ 必须"进程级单例代理 + 一进程一实例"。这也说明**代理模式不能替代多进程模型**，"宿主进程启动 guest"≠"一个进程"。
4. **幂等性**。同一实例、同一 API 必须永远返回同一字符串。任何"每次调用生成新路径/新随机后缀"的实现都会自毁一致性。

### 5.5 顺带一个"零成本高收益"的替代：放弃伪造 UID

比路径欺骗划算得多的一件事：**让 `Process.myUid()` 直接返回宿主真实 UID**，与 `PackageManager` 返回的 uid **保持一致**（即 NestSpace 的 `observedUid = hostUid` 策略）。

- 这样最常见的检测 **#1「UID 矛盾」直接消失**，且不需要任何伪造；
- 反而"伪造合成 UID"会引入 native `getuid` / `/proc/self/status` / PM 三处不一致，是自找麻烦。

**结论：该省的不是"路径层"，而是"伪造冲动"。**

### 5.6 推荐路线：按需加层（与 NestSpace 相反的顺序）

```
第 1 步  方案 A：真路径 + 中性命名 + 零 native hook + observedUid=hostUid
         → 用探测 APK 量化"被检测率"，跑通启动链与多进程
第 2 步  只对"实测失败的 App"启用方案 B（单前缀 4 符号替换）
         → 用 feature flag 开关，逐 App 灰度
第 3 步  只有当目标 App 明确读 /proc/self/maps 做特征扫描时，才补 facade 的单项
         → 永不做"全量 facade 默认开启"
```

**验收指标（方案 A 就应达成）**：环境一致性探针 4 项全过（包名 / UID / dataDir / APK 路径在 Java 与 PM 间一致）、冷启动成功、多进程 App 正常、`/proc/self/maps` 里无"virtual/blackbox"类特征字符串（这条靠命名而非 hook 达成）。

### 5.7 一句话回答

> **你的方向是对的，但收益不来自"更强的代理"，而来自"更少的欺骗"**：
> 放弃"让 guest 看到 `/data/user/0/<guest>`"这个目标，就能一次性去掉 libc hook、`/proc` 合成、fd 映射、property 伪造四层；代价只是 **dataDir 前缀会带宿主包名**——这一条在普通 App 上几乎没人检测，在强风控 App 上则没有低成本解法，应在产品层面直接划为"不支持"。
>
> 注意：省下的是**路径层**，省不掉的是**占位组件、ActivityThread 启动链、多进程槽位、BAL、DCL 只读、WebView 多实例**——这些才是 NestSpace/ShitJumpingVM 的主要工作量所在。

---

## 6. 专题 A：地址 / 路径映射（第一难点）

### 5.1 问题本质

guest 认为自己是 `/data/user/0/<guest_pkg>`，内核层面只能是宿主目录。**每一层观察面必须给同一个答案**，否则"不被发现"立刻崩塌。

### 5.2 三层映射

**第 1 层：Java Framework**
- `Context.getDataDir()/getFilesDir()/getCacheDir()/getNoBackupFilesDir()/getExternalFilesDir()`
- `ApplicationInfo.sourceDir` / `publicSourceDir` / `splitSourceDirs` / `nativeLibraryDir`
- `PackageManager` 查询结果；SharedPreferences / SQLite / MMKV / Keva 的底层路径
- 实现：虚拟 `ContextWrapper` + 重建 `LoadedApk`（NestSpace 的 `ContainerContext`，ShitJumpingVM 的 `VirtualContext`）

**第 2 层：native（决定成败）**
- GOT/PLT patch 覆盖：`open/open64/openat/openat2/openat64/fopen/close/dup/dup2/dup3/fcntl/access/faccessat/stat/lstat/fstat/statx/readlink/readlinkat/getxattr`
- **直接 syscall 绕过**：`syscall(SYS_openat,…)`、`syscall(SYS_statx,…)`、内联 `svc` —— NestSpace 明确把内联 `svc` 标记为不可覆盖（`unverified`），必须在诊断中如实上报
- 路径前缀索引（**不要用正则**）：`/data/user/0/<guest>` → `<host files>/vspace/data/<guest>/<instance>`；归一化 `/data/data` ↔ `/data/user/0`、`/sdcard` ↔ `/storage/emulated/0`
- `mmap` / `sendfile` 作用于已打开虚拟 fd 时保持一致（否则 maps 泄露真路径）
- **每实例独立 linker namespace**，否则同名 `.so`、JNI 全局符号会串

**第 3 层：`/proc` 与 system property 合成**
- `/proc/self/status`（Name/Uid/Gid）、`/proc/self/cmdline`、`/proc/self/maps`、`/proc/self/mountinfo`、`/proc/self/attr/current`
- 实现：命中这些路径时返回**合成的只读临时 fd / `FILE*`**，不放行真实文件
- **fd → 虚拟路径映射表**必须跟 `dup/dup2/dup3/fcntl/close` 同步，否则 `readlink("/proc/self/fd/N")` 直接泄露宿主路径
- `stat/fstat` 的 `st_uid/st_gid/st_dev/st_ino` 也是观察面；只改字符串不改元数据 = 自相矛盾
- property：`__system_property_get/find/read_callback/read/serial` 返回 Java `Build` 快照，未知键回退真实后端

### 5.3 "内存地址映射"的真相

guest 的 dex/so 必然映射在宿主地址空间，`/proc/self/maps` 里天然出现两个 `base.apk`：

```bash
adb shell "cat /proc/<pid>/maps | grep base.apk"
# 可疑：宿主 base.apk 与 guest base.apk 同进程映射
```

所以"地址映射"在用户态工程上不是改页表，而是**在观察口子上把不该出现的行过滤/改写**（maps 合成、`dladdr` 路径改写、`dl_iterate_phdr` 一致性）。这是纯用户态的上限。

### 5.4 工程约束（NestSpace 文档给出的指标，可作我方预算基线）

- 路径查询 p95 < 2 µs；单次映射 0 次堆分配；native env 启动注册 < 20 ms；常驻 < 3 MB
- native env 快照不可变 + 原子指针发布；**禁止在 syscall hook 里回调 Java、加全局锁、同步写日志**
- 只 patch 当前实例的 native 库，绝不 patch 系统库 / 宿主库
- 每条 hook 有独立开关、异常边界、原始 trampoline 备份（禁止 hook 内再调同名函数）
- 子进程必须重新注册 NativeEnvironment
- 16 KB page size 兼容 + arm64-v8a / x86_64 双 ABI

---

## 7. 专题 B：不被寄主（guest）发现

### 6.1 检测方武器库

| # | 检测点 | 原理 | 对抗难度 |
| --- | --- | --- | --- |
| 1 | UID 矛盾：`Process.myUid()` vs `PackageManager.getApplicationInfo().uid` | guest 跑在宿主 UID | 高（Java 可伪造，**native `getuid()` + `/proc/self/status` 必须同步**） |
| 2 | `PendingIntent.getCreatorUid()` | 系统令牌链路，容器常漏改 | 很高 |
| 3 | `/proc/self/maps` 特征扫描（hook 库名、`/virtual/data/` 等） | Hook 注入痕迹 | 中（合成 maps + 中性命名） |
| 4 | `getCanonicalPath()` 含 `virtual` 等字样 | 绕过 Java 路径伪造 | 高（目录命名与 canonical 一致性） |
| 5 | 系统服务被替换为 `java.lang.reflect.Proxy` | Hook 痕迹 | 中（加固壳同样代理，误报高，通常只作辅助） |
| 6 | ClassLoader 链异常 | 自定义 ClassLoader | 低价值（误报极高） |
| 7 | 故意抛 NPE 观察崩溃是否被吞/改写 | 容器拦截 crash 路径 | 中 |
| 8 | 服务端 Play Integrity / Key Attestation | 硬件级证明 | **不可跨越** |
| 9 | 特权身份查询（`getDataNetworkTypeForSubscriber` 抛 `SecurityException`） | 宿主 UID 无特权电话权限 | **不可跨越**，只能降级并诊断 |

来源：[HackTricks](https://hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/android-application-level-virtualization.html)、[Zahid's Blog](https://blog.azzahid.com/posts/android-app-virtualization/)、[Android 双开环境底层剖析与风控检测落地](https://jishuzhan.net/article/2116102765970456578)。

### 6.2 唯一可持续的做法：一份事实，多个观察面

```
VirtualEnvironment（唯一不可变事实源）
    ├─ Java Framework Facade
    ├─ Native syscall / proc Facade
    ├─ Virtual Package Manager
    ├─ Virtual Services / Content / AttributionSource
    └─ Diagnostics（一致性探针）
```

必须用测试强制的**一致性不变量**：

```
PackageManager 包名 == Context.getPackageName() == ApplicationInfo.packageName
Process.myUid()(Java) == getuid/geteuid(native) == /proc/self/status 的 Uid == PM 返回 uid
Context.getDataDir() == stat/open 路径 == ApplicationInfo.dataDir == maps 中可见路径
Build / Android ID / DID == Settings 查询 == 请求公共参数 == 日志值
nativeLibraryDir == dlopen 成功路径 == maps 中模块路径
```

安全默认值（NestSpace 结论，值得沿用）：

```
当前运行的 guest 包：observedUid = hostUid（避免与 PM 矛盾）
其他未运行虚拟包：   observedUid = 稳定合成 UID
```

### 6.3 架构级约束

- 不做"全量 patch 的通用 ART Hook 框架"，只对"明确观察点 + 可回滚"的符号下手
- **Host / Target 调用窗口**：host 调真实系统服务时切回 host 身份，窗口可嵌套、异常安全
- 安全 SDK 探针只在 target 调用窗口开启
- hook 库最小化、可关闭；**不承诺"隐藏全部映射"**
- 检测是军备竞赛 ⇒ 产品层提前定义"不支持的 App 类别"，而不是承诺隐身

---

## 8. 专题 C：进程间资源管理

### 7.1 为什么必须多进程

`ActivityThread`、`Instrumentation`、`LoadedApk`、主 `Looper`、资源管理器、linker 状态都是单例/近似单例：

- 一个槽位进程**只能服务一个 guest 实例**
- 切实例前彻底销毁 Application / Provider / Activity / Handler / 线程 / native 状态
- 不能在同一进程里并行驱动两个 guest 的主线程生命周期
- `ThreadLocal` 不够用（Binder 回调线程、HandlerThread、native 线程池看不到）⇒ **进程级默认环境 + 宿主调用窗口**

### 7.2 固定槽位池模型（两个非 VA 项目都用这套）

| 维度 | NestSpace | ShitJumpingVM |
| --- | --- | --- |
| 进程命名 | `:c1`–`:c16` | `:p0`–`:p9` |
| 槽位数 | 1–16 可配置；自动模式按可用内存调整上限 | 固定 10 |
| 占位组件 | 未公开数量（按需） | **160 个**：80 Activity / 40 Service / 25 Provider / 15 Receiver |
| 分配器 | 主进程集中分配/回收（`ContainerProcessBrokerService`） | 内置进程槽管理 |
| 回收策略 | **LRU：优先回收最久未使用的后台容器，前台不回收** | — |
| 复用安全 | 复用前结束旧进程，避免同名 native 库与 JNI 全局状态互相污染 | — |
| 崩溃隔离 | 目标主线程致命异常 ⇒ 记录崩溃 + **只重启对应槽位** | 逐进程 breadcrumb 诊断日志 |

要点：
- 进程名必须静态声明在宿主 manifest，运行时无法新增
- 组件数量决定并发上限；**一个多进程 App 会吃掉多个槽位**（`android:process=":push"` → 独立槽位），容量要按实例总槽位算，不能按"打开的 App 数"算
- 目标子进程里 Activity/Service/Provider/广播 都需要跨槽位路由

### 7.3 复用与清理清单（最易出 bug 的地方）

- 复用前：销毁所有 Application / Provider / Activity / Handler / 线程 → **native 状态完全重建**（不可复用旧 `NativeVirtualEnvironment`）
- DID / Android ID / Cookie 关联对象不得在同一槽位保留多实例副本
- 每实例独立：linker namespace、WebView `data-directory suffix`、诊断文件、AccountManager 代理视图
- 崩溃隔离：主线程未捕获异常 ⇒ 结束该槽位（不要静默吞）；后台线程可隔离异常记录后继续
- `fork` / `posix_spawn` / `exec`：`exec` 后必然丢失 hook 与 Session，普通 APK 通常也不允许从私有目录执行二进制 ⇒ 标 `unsupported` / 高风险
- Binder 归因：Java 代理只覆盖被代理接口；native `libbinder`/`Parcel`/`ioctl` 直连无法保证；系统服务永远看到 `hostUid` ⇒ 诊断区分"Java 代理成功"与"系统侧接受虚拟身份"

---

## 9. 不可跨越的边界（立项 PRD 必须写）

1. 无法改宿主 Binder 的**真实调用者 UID**
2. 无法把进程切到目标 **SELinux 域**
3. 无法创建真实 **Android 用户 / PID namespace / mount namespace**
4. 无法生成服务端不可区分的**硬件级身份 / 完整性证明**
5. 无法让 `system_server` / 外部进程看到完整虚拟进程表与安装状态
6. 无法提供按包隔离的 **AndroidKeyStore namespace**、系统认可的自定义 Account 类型
7. 无法支持**特权蜂窝身份**（只能降级为 `UNSUPPORTED_PRIVILEGED_TELEPHONY`，**不得伪造返回值**——会被其他 SDK 交叉读取，制造矛盾事实）
8. 实例隔离是**产品级逻辑隔离**，同宿主 UID 下恶意 native 代码仍可读到宿主可见数据 ⇒ 不是安全沙箱
9. **不要自研 ART 级虚拟机/DEX 解释器**：同作者 `android-vm` 的两周试错 + NestSpace README 的自述已证明商业加固 App 无法启动

---

## 10. 实施路线（对齐目标系统基线）

目标系统：**Android 14 / 15 / 16（API 34 / 35 / 36）为主支持，API 33 降级，不追 Android 17**（数据与理由见 `02-AOSP-目标系统基线.md`）。

| 阶段 | 交付 | 验收 |
| --- | --- | --- |
| 0 基线 | 原生/容器对照工具、兼容矩阵、结构化诊断导出 | 能定位是 Java / native / 系统服务 / 网络 / UI 哪层失败 |
| 1 统一环境 | `VirtualEnvironment` 事实源 + JNI 快照 + 一致性校验 | 四路读取一致 |
| 2 进程启动 | 统一 Application/Provider 启动链；槽位池 + Broker；子进程路由；LRU 回收 | 冷启动不依赖固定 settle；槽位复用无残留 |
| 3 Native Facade | 路径映射、`/proc` 合成、property、fd 映射、直接 syscall 探针 | 与 Java 一致；未覆盖标 `unverified` |
| 4 组件与服务 | Service/Provider/Broadcast/Job/Notification + attribution 代理 | 多进程 App、WebView App 不回归 |
| 5 反检测与性能 | 选择性探针、hook 故障隔离、路径缓存、零拷贝帧 | 启动回归 ≤5%；每条 hook 可单独关闭 |

**反检测验收必须先有测试再写 hook**：为每个检测点准备自建探测 APK（UID 三路、maps、canonical path、PendingIntent、NPE 崩溃行为、Proxy 痕迹），能力状态只报 `verified / available-unexercised / unverified / unsupported`。

---

## 11. 参考链接

**项目（均已确认非 VA 系）**
- https://github.com/chuccp/NestSpace ｜ 本地副本：`_ref/NestSpace`（浅克隆）
- https://github.com/woand/ShitJumpingVM
- https://github.com/TanvirHossain2/NEXTVM ｜ 本地副本：`_ref/NEXTVM`（ShitJumpingVM 的上游快照，仅 4 个提交）
- https://github.com/chuccp/android-vm ｜ 本地副本：`_ref/android-vm`（自研 DEX 运行时，边界案例，见 3.5）
- https://github.com/obadadallo95/mirro-android-virtualization
- https://github.com/LSPosed/LSPatch

**AOSP 规范（已本地归档）**
- 归档目录 `_ref/aosp/raw/`（11 个页面，HTML + 纯文本），说明见 `_ref/aosp/README.md`
- 抓取工具：`tools/fetch-aosp.mjs`（Node + `127.0.0.1:12450` 代理 + HTTP CONNECT + 跟随跳转）、`tools/html2txt.py`
- 目标系统与逐版本限制：`02-AOSP-目标系统基线.md`

**本系列其他调查文档**
- `02-AOSP-目标系统基线.md` —— 支持范围（14/15/16）与逐版本平台限制
- `03-标准路径与沙箱文件系统.md` —— 标准路径必要性、路径视图层设计、FUSE/ptrace 可行性排除
- `04-Guest启动路径与控制权转交.md` —— ActivityThread 启动链、两个介入点、权限代理机制
- `05-从Espresso与测试框架借鉴的设计模式.md` —— 声明式拦截、Idling 同步、root+相对路径模型
- `06-路径请求分级与宿主处理策略.md` —— L0–L6 分级、五种动作、决策树与验收矩阵
- `07-Guest进程路径承接与两种姿态.md` —— 承接时机（W1/W2）、7 组入口、欺骗姿态 vs 支持姿态

**原理与检测**
- HackTricks — Android Application-Level Virtualization：https://hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/android-application-level-virtualization.html
- Zahid's Blog — What Is Android App-Level Virtualization：https://blog.azzahid.com/posts/android-app-virtualization/
- 双开环境底层剖析与风控检测：https://jishuzhan.net/article/2116102765970456578
- 学术：Towards Transparent and Stealthy Android OS Sandboxing：https://par.nsf.gov/servlets/purl/10346867

---

## 12. 待确认事项

1. **路径方案是否按"方案 A 起步"**：即"真路径 + 中性命名 + 零 native hook + `observedUid=hostUid`"（见第 5 章）。这是本调研给出的最大复杂度削减点，需要你拍板
2. **NestSpace 是否作为代码起点**：建议"摘取实现 + 重写 UI/剥离 AI 与插件平台"，而非整仓演进（理由见 3.3）
3. **ART hook 是否需要**：若不需要，可完全避开 LGPL 纠缠
4. **实例数量需求**：单实例即可大幅减少槽位与占位组件工程量
5. **App 类别白名单**：加固、强完整性校验、金融支付默认不支持
6. **许可证合规页**：APP 内需保留 Apache-2.0 文本与版权声明（NestSpace 及所有 Apache-2.0 依赖）
7. **是否参考 NEXTVM**：结论是**不参考**（其内容已被 ShitJumpingVM 覆盖且后者更完整），仅保留 `_ref/NEXTVM` 作为溯源
