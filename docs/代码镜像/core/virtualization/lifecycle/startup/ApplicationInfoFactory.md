# ApplicationInfoFactory

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/startup/ApplicationInfoFactory.kt`
> 来源：B4-4b 从 `VirtualApplicationManager.buildApplicationInfo` 原样搬出（2026-11-26）

## 职责

**只回答一个问题：交给 guest 的 `ApplicationInfo` 长什么样（沙箱路径 + APK metaData）。**

- 负责：按 `packageName`/`apkPath`/`virtualContext` 填 `ApplicationInfo` 的基础字段；
  从 guest APK 解析 `metaData` 并补 GMS 版本号；`isPathFidelityGuest` 为真时把 4 个路径字段换成合成路径。
- **不负责**：`ApplicationInfo` 的消费方（`LoadedApk` 补丁、`VirtualContext` 路径翻译）、
  `ApplicationInfo` 的注册（那是 VPMS / `PackageManagerProxy` 的事）。

## 函数

| 签名 | 作用 | 关键副作用 / 前提 |
| --- | --- | --- |
| `build(packageName: String, apkPath: String, virtualContext: Context): ApplicationInfo` | 造 `ApplicationInfo` | 只读：会调 `virtualContext.packageManager.getPackageArchiveInfo(...)`；解析失败时退回"只有 GMS 版本号"的 metaData |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| GMS 版本号硬编码 `243431006` | 上游既有实现：guest APK 没声明 `com.google.android.gms.version` 时补一个 | **中性偏差**（容器自造），保留原值 | metaData 解析失败的 warning 日志里可见 |
| `isPathFidelityGuest` 分支 | `v135` 实验：对指定包报告"标准安装路径"，真实 I/O 由 v135 规则重定向 | **必要偏差**（路径欺骗，见 `架构-syntheticDataDir.md`） | 探针 J 系列 + `PathKinds` golden |

**等价性结论（规则 10）**：`build` 与旧 `buildApplicationInfo` **逐字等价（1 296 字符）**：

```powershell
git -C _ref/ShitJumpingVM show <上一提交>:<VAM 路径> > tools/tmp/vam-old2.kt
node tools/lint/check-refactor-equivalence.mjs --old-file tools/tmp/vam-old2.kt --old-fn buildApplicationInfo `
  --new-file <ApplicationInfoFactory.kt> --new-fn build
```

## 约束与坑

- **不可纯 JVM 单测**：`ApplicationInfo()` / `Bundle` / `PackageManager` 在 unit test 的 android.jar 里是
  `Stub!` 桩，构造即抛 `RuntimeException`。本文件因此**没有 `*Test.kt`**，行为由三层覆盖：
  ① `VirtualConstants.isPathFidelityGuest/syntheticDataDir` 等纯数据函数（`core:model` 已有测试）；
  ② 真机探针 J 系列（`getApplicationInfo` 的 dataDir/sourceDir 字段）；
  ③ breadcrumb/日志（metaData 解析失败会留 warning）。
- `metaData` 必须**原样**指向 APK 里解析出的 `Bundle`：SDK（Firebase / AppLovin / Cronet）在
  `Application.onCreate` 之前就读它；改成复制会丢 `ApplicationInfo` 里其它字段的联动。
- `sourceDir` / `publicSourceDir` / `dataDir` / `nativeLibraryDir` 四个字段是路径欺骗的对外窗口，
  改动必须同步 `docs/架构/架构-syntheticDataDir.md` 的白名单说明。
- 本函数**不写文件、不改全局状态**，所以可以在任意线程调用（当前调用方在 bind 线程）。
