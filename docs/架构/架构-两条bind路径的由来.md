# 架构：两条 bind 路径的由来（legacy vs framework）

- 日期：2026-11-26
- **核实基准**：容器仓库完整历史（`git fetch --deepen=300` 后共 215 个提交；fork 点 `63346e6`，上游 75 / 我们 140）
- 状态：已核实（逐 commit 的 `-S` 检索 + 上游 commit message 原文）
- 回答：**legacy 是何时、为什么出现的；framework 是何时、为什么取代它；为什么不是删掉 legacy，而是并存**

---

## 一、时间线（全部有 commit 证据）

| 时间 | commit | 归属 | 事件 |
| --- | --- | --- | --- |
| 2026-03-07 | `7cdcf01` **Initial release: NEXTVM Android Virtual Environment Engine** | 上游 | **legacy 路径诞生**：反射 `new` Application，然后手动 attach 到"宿主 LoadedApk（打补丁字段）支撑的 VirtualContext" |
| 2026-10-10 | `792ae28` **feat: build guest class loaders through framework LoadedApk (B1)** | 上游 | 第一步"对象图真实化"：guest 的 ClassLoader 改由 framework LoadedApk 建 |
| 2026-10-10 | `40d5f88` **feat: framework-native guest Application bind via LoadedApk (B2)** | 上游 | **framework 路径诞生**：`ContextImpl.createAppContext(loadedApk)` + `LoadedApk.makeApplication()`；legacy **降级为 fallback**（+118/−1，legacy 实现保留） |
| 2026-10-10 | `e590c32` seccomp supervision + **system-facing identity fix** | 上游 | framework LoadedApk 的 `mPackageName` 被 patch 成 **host 包名**（golden 里 `framework bind identity … mPackageName=com.nextvm.app ai.packageName=mark.via` 就是这条） |
| 2026-10-20 | `8d77b94` splash-stall fix (v39) + start-activity channel hunt (v35–v44) | 上游 | 加 `legacy attachBaseContext enter/exit` 埋点 —— 说明那时 legacy 仍在被观测 |
| 2026-11-18 | `63346e6` v156 | 上游 | **我们的 fork 点**（分支 `baseline-v150` 起点） |
| 2026-11-24 | `00673c9` → `612cef5` Revert | **我们** | 试把 dataDir patch 挂到 framework 路径 → 当天 revert（注入点无效，`VirtualContext.super` 拿的是容器自己的 LoadedApk） |
| 2026-11-26 | `58a5d42` | **我们** | 加 `DebugFlags.FRAMEWORK_BIND`（`setprop debug.nextvm.frameworkBind 0` 强制 legacy，仅 debug 包） |
| 2026-11-26 | `c0a9245` | **我们** | 8 份 golden + 契约 C12 把两条路径各自钉住 |

**改动频率（说明谁在动）**

| 对象 | 被改动 |
| --- | --- |
| `VirtualApplicationManager.kt`（两条路径的宿主） | framework 引入后共 **61** 次（上游 34 + 我们 27） |
| `bindViaFrameworkLoadedApk` 本体 | **3** 次：`40d5f88` 引入、`00673c9` 加 patch、`612cef5` revert |
| `createApplication` / legacy 装配 | 引入后长期只被"加埋点/patch"类改动波及，**fork 后从未被真机走过**（直到 2026-11-26 采 golden） |

## 二、为什么设计成两条路径（上游 `40d5f88` 的原文理由）

> "The new framework branch builds the app ContextImpl via `ContextImpl.createAppContext(loadedApk)` and lets
> `LoadedApk.makeApplication()` instantiate and attach the guest Application — **replacing the legacy path that
> reflection-created the Application and attached it to a VirtualContext backed by the HOST's LoadedApk with patched fields**.
> Falls back to the legacy bind on any failure. Also fills `ApplicationInfo.className` from the install parse
> (without it makeApplication silently substitutes a default Application and packer stubs never run)."

拆成三条可核对的动机：

1. **消除"合成对象图"**：legacy 的 Application 是容器反射造的，attach 的 Context 背后是**宿主**的
   LoadedApk + 一堆打过补丁的字段 → 对象图与真实应用"构造方式不同"，属可检测面。
   framework 路径让 framework 自己 `makeApplication` → 对象图与真实应用同构。
2. **让加固壳的 stub Application 真的跑起来**：`ApplicationInfo.className` 必须来自安装解析，
   否则 `makeApplication` 会静默换成默认 `Application`，壳的 stub 永不执行（这正是"壳 guest 不出 banner"的根因之一）。
3. **验证价值**：同一提交记录了中国联通壳 `com.secneo.apkwrapper.AW` 能被 framework 造出来并打印 banner，
   据此**排除**了"合成对象图（B1 CL + B2 LoadedApk/Context/attach）"这一族失败原因 —— 把排查范围推到
   GOT hook 层以下的通道（direct svc / dladdr / linker truth）。

## 三、为什么保留 legacy（而不是删掉）

| 原因 | 具体 |
| --- | --- |
| framework 路径依赖 framework 内部 API | `getPackageInfoNoCheck` / `ContextImpl.createAppContext` / `makeApplication` / `makeApplicationInner`，跨版本会变；`10d5f88` 自己也写了 "`makeApplication` / `makeApplicationInner`" 双签名回退 |
| 失败必须能降级 | `createFrameworkLoadedApk` 任何一步返回 null（split 信息、`ApplicationInfo` 解析、反射失败）就走 legacy |
| 历史兼容 | legacy 是 2026-03 → 2026-10 期间唯一路径，大量行为细节（`injectGuestPathsIntoLoadedApk`、`patchJiaguStubBeforeAttach`、`installRuntimeLoadCallerFix`）只在那里 |

**所以两条路径不是"新旧并存随便选"，而是"主路径 + 唯一可用的回退"** —— 这也解释了为什么
legacy 长期缺乏真机覆盖：默认永远走 framework，legacy 只在异常时才出现，因此从没被当回归对象。

## 四、两条路径的具体差异（2026-11-26 用 8 份 golden 实测）

详见 `架构-启动链契约.md` §二。三处**实质**差异：

| 差异 | framework | legacy |
| --- | --- | --- |
| `v138-appctx mInitialApplication` | 不改（`-> com.nextvm.app`） | 改成 guest（`-> mark.via`） |
| via 的 `androidx.startup.InitializationProvider` | FAILED（emoji2 CNFE） | ok |
| `v143-datadir` 源路径 | 沙箱 dataDir → 虚拟路径 | 已是虚拟路径 → 虚拟路径（幂等重设） |

legacy 的 `attachBaseContext` 里有 `injectGuestPathsIntoLoadedApk` / `patchJiaguStubBeforeAttach` /
`installRuntimeLoadCallerFix` 三段 patch，framework 路径没有（它靠 framework 自己 attach）——
这是"两条路径行为不一致"最具体的代码位置，也是 `STATUS.md` §2 #9 待定论的地方。

## 五、我们 fork 之后做了什么（只列与路径选择相关的）

| 项 | 结论 |
| --- | --- |
| 有没有改默认路径 | **没有**。默认仍是 framework，选择逻辑一行未动 |
| 加过什么 | `DebugFlags.FRAMEWORK_BIND`（默认 true，仅 debug 包可切）—— 只为采集 legacy 样本 |
| 试过什么又回退 | `00673c9` 把 dataDir patch 挂 framework 路径 → `612cef5` revert（注入点无效） |
| 现在有什么护栏 | 8 份 golden × C1–C12：两条路径各自的装配特征、provider 失败降级、豁免分支、两次一致 |

## 六、已知风险

1. **两条路径行为不一致且未定论**（`STATUS.md` §2 #9）：`mInitialApplication` 身份、provider 成功率。
   **拆分 `bindApplication` 时不许顺手对齐** —— 那属于行为变更，要走变更流程。
2. legacy 的 patch 三重奏（`injectGuestPathsIntoLoadedApk` / `patchJiaguStubBeforeAttach` /
   `installRuntimeLoadCallerFix`）只在 legacy 生效 → 如果哪天 framework 路径成为唯一路径，这三段会被误删。
3. `frameworkBind` 是 debug-only 开关；release 包里不存在该分支（`DebugFlags.ENABLED = BuildConfig.DEBUG`）。
