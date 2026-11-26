# GuestLoadedApkPathInjection

> 代码：`core/virtualization/src/main/java/com/nextvm/core/virtualization/lifecycle/startup/GuestLoadedApkPathInjection.kt`
> 来源：2026-11-27 B4-4f 从 `VirtualApplicationManager.injectGuestPathsIntoLoadedApk` 原样搬出（等价检查 ✓）

## 职责

**只回答一个问题：`Application.attach()` 之前，宿主 LoadedApk 里的路径要不要改成 guest 的。**

- 负责：沿 `ContextWrapper` 链找到 `ContextImpl.mPackageInfo`（LoadedApk），把它的
  `mApplicationInfo` 的 4 个路径字段与 `mLibDir` 改成 guest 的值。
- **不负责**：构造 guest 的 `ApplicationInfo`（`ApplicationInfoFactory`）、真正 attach（`VAM.attachBaseContext`）、
  壳修正（`PackerAttachFixes`）。

## 函数

| 签名 | 作用 | 关键前提 / 副作用 |
| --- | --- | --- |
| `apply(virtualContext: Context)` | 把 guest 路径写进宿主 LoadedApk | 必须在 `Application.attach()` **之前**调用（attach 会把 `mPackageInfo` 固化到 Application）；失败只打 W 不抛 |
| `internal val APP_INFO_FIELDS` | 注入的字段清单（顺序契约） | 仅文档/测试用；漏字段 = 静默失灵，`GuestLoadedApkPathInjectionTest.I1` 固定 |

## 偏差登记

| 偏差 | 内容 | 类型 | 观测 |
| --- | --- | --- | --- |
| 无（纯搬迁） | `check-refactor-equivalence` 结果：**等价 ✓**（归一化后逐字相同，1063 字符） | 无偏差 | 同上 |
| 新增 `APP_INFO_FIELDS` | 原实现是 4 行逐字赋值，没有显式清单 | 中性偏差（可审查性） | `I1` |

## 约束与坑

- **顺序敏感**：必须在 `Application.attach()` 之前（VAM 的 `attachBaseContext` 第一句）。
  attach 之后再改 `mPackageInfo` 已经晚了 —— `Application.mLoadedApk` 已指向旧对象。
- 找不到 `mPackageInfo` / `mApplicationInfo` 时**只告警返回**，不能抛：attach 流程不能因为注入失败中断。
- `mLibDir` 是 `Runtime.nativeLoad()` 构造 linker namespace 的路径来源；漏了它 = `UnsatisfiedLinkError`（壳应用最先炸在这）。
- 这是 **legacy 路径专属**（framework 路径由 `LoadedApk.makeApplication` 自己构造，天然是 guest 对象）；
  若将来要"对齐 framework"，属于行为变更（L6），不要藏在这个文件里。
