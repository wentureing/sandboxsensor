# Guest 进程的路径承接模型，与「欺骗姿态 vs 支持姿态」

- 调查日期：2026-11-21
- 回答两个问题：
  1. 如果 guest 进程由宿主创建，**路径行为怎样被宿主完全承接与代理**？
  2. 如果把 guest 看成"运行在宿主上层、受宿主支持的进程"，**能否更轻松地编码以骗过 guest？能不能做到？**

---

## 1. 先纠正前提：「guest 进程由宿主创建」在 Android 上只有一种合法形态

| 形态 | 可行性 | 原因 |
| --- | --- | --- |
| 宿主 `fork()`/`exec()` 一个二进制来跑 guest | ❌ **不可能** | **Android 10 W^X 限制**：targetSdk ≥29 后应用无法执行自己数据目录里的文件（Wine 为此专门下调 targetSdk 规避：["lower targetSdkVersion to avoid Android 10 W^X restrictions"](https://list.winehq.org/hyperkitty/list/wine-gitlab@winehq.org/message/MYB4FSTR3BXY4EQ6BIX3VRQG3KDPF33V/)）。且即使能 exec，也拿不到 ART/Binder 初始化环境 |
| 宿主 clone 一个自己的进程 | ❌ | 无 `clone`/namespace 权限 |
| **宿主在 manifest 声明 `android:process=":cN"`，由 Zygote fork** | ✅ **唯一合法形态** | 系统按宿主的 `ApplicationInfo` 初始化该进程，代码来自宿主 APK |

**所以"宿主创建 guest 进程"的准确含义是：宿主通过 manifest 预留进程槽位，由系统（Zygote）创建进程，但进程的初始化流程（`bindApplication`）在宿主代码里——宿主"创建"的是初始化流程，不是进程。**

**而这恰好是"完全承接"的最佳时机**：在 guest 第一行代码执行之前，宿主已经在进程里了。

---

## 2. 承接的时机模型：三个安装窗口

Android 的进程初始化顺序（关键：**ContentProvider 早于 Application.onCreate**）：

```
Zygote fork
 └─ ActivityThread.main()
     └─ attachApplication → handleBindApplication()
         ├─ ★ W1：创建/获取 LoadedApk，建立 ClassLoader 之前    ← native 环境注册的唯一安全窗口
         ├─ makeApplication(): Application.attachBaseContext()
         ├─ ★ W2：installContentProviders()（Provider.onCreate 在这里！）← 必须早于这里完成 Context 替换
         ├─ Application.onCreate()
         └─ ★ W3：handleLaunchActivity()（首个 guest Activity）
```

| 窗口 | 时机 | 必须完成什么 | 漏掉的后果 |
| --- | --- | --- | --- |
| **W1** | `bindApplication` 最早期、任何 guest 代码之前 | native 环境注册（映射表、沙箱根、能力集） | Provider/native SDK 早期读路径时拿到宿主真实路径 |
| **W2** | `attachBaseContext` → **`installContentProviders` 之前** | Java `Context` 替换、`ApplicationInfo` 虚拟化、PM 代理 | Provider 用宿主 Context 建库 → 数据写进宿主目录 |
| **W3** | 首个 Activity | Instrumentation / ClassLoader / ActivityInfo | 组件路由失败 |

> **这是本节最重要的工程结论：`ContentProvider.onCreate` 早于 `Application.onCreate`，所以"先 Application 再 Provider"的直觉是错的。** 承接必须在 W1/W2 完成，不能等到 `onCreate`。

---

## 3. 「完全承接」= 覆盖 7 组入口，不能只做 Java

| # | 入口组 | 具体内容 | 承接方式 |
| --- | --- | --- | --- |
| 1 | `Context` | `getDataDir/getFilesDir/getCacheDir/getCodeCacheDir/getNoBackupFilesDir/getDatabasePath/getDir/getExternalFilesDir(s)/getExternalCacheDir(s)/getExternalMediaDirs/getObbDir(s)/getExternalStorageDirectory` | 注入式（虚拟 Context） |
| 2 | `ApplicationInfo` | `dataDir/deviceProtectedDataDir/credentialProtectedDataDir/sourceDir/publicSourceDir/splitSourceDirs/nativeLibraryDir` | 注入式 |
| 3 | `PackageManager` | `getApplicationInfo/getPackageInfo/getInstalledPackages` | Binder 代理 + 注入 |
| 4 | `Resources` / `AssetManager` | 资源与 assets 的 APK 路径 | 注入式（rebuild LoadedApk） |
| 5 | `java.io` → JNI | `File/FileInputStream/RandomAccessFile/SQLite/MMKV` 最终都落到 libc | **拦截式（L2）** |
| 6 | libc 符号 | `open/openat/stat/access/readlink/...` | **拦截式（GOT/PLT，只 patch 目标模块）** |
| 7 | 系统服务的路径参数 | `DownloadManager`/`JobScheduler`/`MediaStore` 的 path/uri 参数 | Binder 代理改写 |

**1–4 组是"注入式"（主动权在宿主），5–6 组是"拦截式"（被动），7 组是"代理式"。**

结论：**只做注入式 = 只承接了"通过 Context 的访问"（约占常规访问 70%）；加上拦截式才接近完全承接。** 这也解释了为什么 NEXTVM/ShitJumpingVM 做了 `VirtualContext` 之后仍然要写 `native-hook.cpp`。

---

## 4. 三种承接架构的可行性

| 架构 | 机制 | 可行性 | 评价 |
| --- | --- | --- | --- |
| **注入式** | 替换 Context / ApplicationInfo / LoadedApk | ✅ | 干净、可测、零崩溃风险；但只覆盖"守规矩"的访问 |
| **拦截式** | patch **目标模块**的 GOT/PLT | ✅ | 覆盖硬编码路径；不影响宿主自身 |
| **拦截式（进程 libc）** | inline hook `/system/lib64/libc.so` | ⚠️ | 会连宿主一起改，需要 Host/Target 窗口，崩溃风险高 ⇒ **不采用** |
| **重定向式** | mount namespace / bind mount / FUSE | ❌ | 需 root |
| **重写式** | 静态改写 guest DEX 里的路径常量 | ❌ | 加固、动态加载、DexClassLoader 全部绕过 |

所以"完全承接"的唯一可行组合是：**注入式（W1/W2 完成）+ 目标模块拦截式（L2）+ 系统服务代理（L5）**。

---

## 5. 判定"是否真的完全承接"的三条判据

1. **幂等**：同一路径从任意入口读到的字符串完全相同；
2. **全入口一致**：同一路径从「Context / ApplicationInfo / PM / java.io / libc / 系统服务」六处读到同样结果，且 `exists()` 为真；
3. **失败可诊断**：任何未承接的入口必须显式记为 `unverified`，不允许静默放行。

> 第 2 条是"完全承接"的操作性定义：**写一个探针，从 6 个入口读同一个路径，全部一致才算通过。**

---

## 6. 「把 guest 看成受宿主支持的上层进程」——这个视角到底能省什么

### 6.1 它能省的（真实收益）

| 好处 | 说明 |
| --- | --- |
| **不必伪装成系统** | 既然 guest 是"被支持方"，宿主可以正大光明地给它一个沙箱路径，不需要 native 层假装成 `/data/user/0/<guest>` |
| **接口化** | 把"宿主提供给 guest 的能力"设计成显式接口（resolver / 规则表 / Idling 计数器），代码从"散落的 if"变成"配置 + 表"（见 `05`） |
| **可测** | 接口是纯函数，能脱离真机做单测 |
| **可按 App 灰度** | "支持谁"变成白名单策略，而不是"能不能骗过所有 App" |
| **失败可控** | 不支持的 App 明确拒绝启动（`BLOCKED`），而不是崩溃后难查 |

### 6.2 它不能省的（必须承认）

**guest 是第三方二进制，不会遵守你的接口。** 具体表现：

- 硬编码 `/data/data/<pkg>` 的 SDK 不经过你的 resolver（`03` §2.1）；
- native SDK 直接 `dlopen` 绝对路径、直接 `open`；
- 加固壳用内联 `svc` 绕过 libc。

⇒ **视角转换能省掉"伪装成系统"的那层心理负担和部分 Java 代码，但省不掉那 12 个 libc 符号。**

### 6.3 一个反直觉但要接受的结论

> **"把 guest 当上层进程"是设计视角（帮你把架构做对），"拦截 libc"是实现现实（因为 guest 不配合）。两者不是二选一。**

---

## 7. 两种产品姿态（本次调查最重要的输出）

| | **欺骗姿态**（通用容器） | **支持姿态**（白名单容器） |
| --- | --- | --- |
| 产品叙事 | "能跑任意 APK，看不出是容器" | "经我们验证的 App 在沙箱里跑得更好" |
| 给 guest 的路径 | 虚拟标准路径 `/data/user/0/<guest>`（B1） | 真实沙箱路径（A / B2） |
| native 层 | 必须（12+ 符号 + fd 表） | **可以没有** |
| 覆盖范围 | 尽量多 | 明确白名单 |
| 失败处理 | 尝试兼容、静默降级 | **明确拒绝 + 说明原因** |
| 被检测后果 | 军备竞赛，永远不完 | 不需要赢——用户知道自己在沙箱里 |
| 工程量 | 4×+ | 1× |
| 主要风险 | 一致性维护无底洞、hook 崩溃 | 目标 App 太少 |

**关键判断：这两条路在代码上是同一套架构，只在"root 字符串 + 是否启用 native 层"上分叉。** 所以不必现在二选一 —— 见下。

---

## 8. 「更轻松地骗过 guest」的具体技巧（按性价比排序）

### 零成本（做了就赚，且无风险）

| 技巧 | 收益 |
| --- | --- |
| **`observedUid = hostUid`**（不伪造 UID） | 直接消掉最常见的检测 #1「UID 矛盾」，且不需要任何 hook（`01` §5.5） |
| **`/proc/self/cmdline` 显示 guest 包名** | 成本极低，能骗过大量"看进程名"的粗糙检测 |
| **沙箱目录中性命名**（`.../files/s/<hex>`，不含 virtual/sandbox/包名） | 免掉字符串特征检测 |
| **不用 symlink** | `getCanonicalPath()` 不会解析出真实路径 |
| **错误码用 ENOENT 而非 EACCES** | 保持"不存在"而非"有东西但没权限"的语义（`06` §4.3） |

### 低成本

| 技巧 | 说明 |
| --- | --- |
| 规则表 + 桩响应替代 if-else（`05` §2） | 让"哪些 Intent 被改写"显式可控，顺便产出诊断日志 |
| `PackageManager` 返回完整的虚拟包信息（含签名） | 让 `getPackageInfo` 系列看起来自洽 |
| 虚拟 Context 覆盖 `getExternalStorageDirectory` | 防止写入真实外部存储（既污染设备又暴露） |

### 高成本低收益（明确不做）

| 技巧 | 为什么不做 |
| --- | --- |
| `/proc/self/maps` 合成 | 要连带处理 `dl_iterate_phdr`、`mmap` 一致性；只对"特征扫描型"检测有用 |
| system property 全量伪造 | `Build` 在 Java 层就能改，native 侧收益低 |
| `stat` 元数据（`st_ino/st_dev`）伪造 | 只在"深挖 inode"的检测里有意义 |
| 隐藏 hook 库的 `.so` 名 | 治标；maps 里"多一个 base.apk"这一事实无法消除 |

### 永远做不到

硬件 attestation（Play Integrity / Key Attestation）、特权蜂窝身份、外部观察者（其他 App / adb / system_server）。

---

## 9. 明确结论（回答"能不能做到"）

**问题 1：路径行为能不能被宿主完全承接？**

> **能，但"完全"要按入口定义：**
> - **注入式承接（Context / ApplicationInfo / PM / Resources）：100% 可做到**，且在 W1/W2 窗口内完成；
> - **拦截式承接（libc 导入符号）：可做到约 95%**，余下是内联 `svc`；
> - **`/proc` 与内核视图：只能"合成"，做不到"承接"**；
> - **跨进程观察者：0%**。
>
> 因此"完全承接"的正确表述是：**guest 能观察到的所有路径里，除"内联 svc 与 /proc/maps"外，其余都在宿主控制下。**

**问题 2：把 guest 当成受宿主支持的上层进程，能不能更轻松地骗过它？**

> **能，但要换一个更省力的目标：不是"骗过所有 guest"，而是"让被支持的 guest 正常工作，并让不被支持的明确失败"。**
>
> 视角转换带来三项实质简化：① 不必伪装成系统（可以给真路径）；② 拦截逻辑变成声明式规则表；③ 失败可以明说而不是崩溃。
> 但它**不能**替代 libc 拦截——因为 guest 是第三方二进制，不会遵守宿主给的接口。

### 落地建议（与上一轮"默认 B1"不冲突）

```
架构：按「支持姿态」设计
      ├─ 统一 resolver（root + 相对路径）
      ├─ 规则表 + Idling 计数器
      └─ native 层是可关闭的模块（feature flag）

运行：默认 B1（虚拟标准路径 + 拦截层开）
      ├─ 绝大多数 App 走这条路（兼容优先）
      └─ 若某 App 因 realpath / maps 检测失败
          → 可切 A（真路径 + 关 native 层），并在 UI 上标注「兼容模式」
```

**即：技术默认选 B1（为了兼容），架构按"支持姿态"写（为了可降级、可测试、可维护）。** 这样既不放弃覆盖面，又保留了"零 native 层"这条低风险退路。

---

## 10. 一句话结论

> **guest 进程从来不是宿主"创建"的，只能由 Zygote 按宿主 manifest 的私有进程声明创建——但初始化流程在宿主手里，所以"在 guest 第一行代码之前完成承接"是可行的，只要抓住 W1/W2 两个窗口（注意 Provider 早于 Application.onCreate）。**
>
> **承接的完整形态 = 注入式（4 组 Java 入口）+ 目标模块拦截式（libc）+ 系统服务代理；"完全"的边界就是"内联 svc 与 /proc/maps 之外"。**
>
> 而"把 guest 当受支持的上层进程"这个视角的最大价值，不是让欺骗变容易，而是让**产品目标从"骗过所有 App"变成"支持白名单 App 并明确拒绝其余"**——这能把工程量降到 1/4，代价是放弃通用性。
