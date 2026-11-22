# 用 VirtualEngine 关键片段追溯演进来源

- 日期：2026-11-22
- 方法：从 `VirtualEngine.kt` 提取独特片段 → 本地扫描 → 联网检索 → 交叉验证

---

## 一、提取的关键特征片段

### 1.1 类注释里的高辨识度表述

`core/virtualization/.../VirtualEngine.kt` 头注释：

```
VirtualEngine — The heart of NEXTVM.
Phase 2+ architecture: Uses REAL Android 16 framework code with comprehensive
subsystem orchestration for full guest app lifecycle.

Sub-systems:
- FrameworkApkParser: APK parsing ported from Android 16's ApkLiteParseUtils
- VirtualServiceManager: Hosts VAMS, VPMS, service/broadcast/provider/intent/perm/network/GMS
- SystemServiceProxyManager: 35 Android system service proxies
- AntiDetectionEngine: Root/emulator/virtual environment detection bypass
...
Initialization sequence (MUST follow this order):
1. bypassHiddenApis() ... 10. loadInstalledApps()
```

**四个可搜索的锚点**：
| 锚点 | 性质 |
| --- | --- |
| `"VAMS, VPMS"` | 缩写，**VirtualApp 家族的命名习惯** |
| `"Android 16's ApkLiteParseUtils"` | **AOSP 16** 的类名，说明代码来源 |
| `"35 Android system service proxies"` | 具体数字，可指纹化 |
| `"Phase 2+ architecture"` | 项目自述 |

### 1.2 我们自己的独特类名（反向搜索用）

| 类名 | 全仓库出现次数 |
| --- | ---: |
| `GuestBreadcrumb` | **199** |
| `VPMS` | 17 |
| `StubRegistry` | 17 |
| `NextVmInstrumentation` | 12 |
| `ApkLiteParseUtils` | 10 |
| `VAMS` | 8 |
| `DexMemDump` | 3 |

---

## 二、本地扫描：与 VirtualApp 的关系

VirtualApp（VA）家族的**代码级独特标识**全部为 **0**：

| 标识 | 出现次数 |
| --- | ---: |
| `com.lody.virtual` | **0** |
| `VirtualCore` | **0** |
| `VAppManagerService` | **0** |
| `VActivityManager` | **0** |
| `VPackageManagerService` | **0** |
| `VClient` | **0** |
| `VirtualRuntime` | **0** |
| `lody`（作者名） | **0** |

→ **没有任何 VirtualApp 的代码派生痕迹。**

同时确认**有 AOSP 代码移植**：
- `VirtualApplicationManager.kt:36` 有 `Copyright (C) 20xx The Android Open Source Project`
- `VirtualConfigurationManager.kt:46` 同上

---

## 三、联网检索结果

| 检索 | 结果 |
| --- | --- |
| `"VAMS" "VPMS" VirtualApp` | 指向 VirtualApp 家族的 `VActivityManagerService`/`VPackageManagerService` —— **命名同源，但我们的类名是 `Virtual*` 全称** |
| `"ApkLiteParseUtils" Android 16` | 命中 [AOSP 源码](https://cs.android.com/android/platform/superproject/main/+/main:frameworks/base/core/java/android/content/pm/parsing/ApkLiteParseUtils.java) —— 确认是 AOSP 16 的类 |
| `VirtualEngine "SystemServiceProxyManager" "AntiDetectionEngine"` | 未命中任何开源项目，说明这些类名是**自有命名** |

### 3.1 决定性发现：README 里写了上游

`README.md` L30 / L244–246：

```
ShitJumpingVM is a fork and rename of NEXTVM. It does not emulate hardware and...
> Note: ShitJumpingVM is a fork of NEXTVM
> (upstream: https://github.com/TanvirHossain2/NEXTVM). The original
> NEXTVM README's feature lists, roadmap, and comparison tables were...
```

`README` L42–43 也解释了包名：

```
> host app's applicationId / namespace is still com.nextvm.app — renaming
> it was not part of the fork, so you will still see com.nextvm in code
```

---

## 四、演进链（确定）

```
TanvirHossain2/NEXTVM            ← 原始项目（Apache-2.0）
        │ fork + rename
        ▼
woand/ShitJumpingVM              ← 另一个 fork（README 里 git clone 指向它）
        │ fork
        ▼
wentureing/ShitJumpingVM         ← 本项目使用的骨架
        │ 我们在此基础上打补丁（p1–p19 + 恢复的 CmdReceiver）
        ▼
sandboxsensor / 当前容器
```

**许可**：Apache License 2.0（`LICENSE` 文件确认）。

**版本线索**：fork 初始提交 `63346e6` 的 subject 是
`v156: trace the login page load window (LoginActivity first resume, 256MB, 20s, same filter)...`
—— 说明上游是一个**长期迭代到 v156** 的项目，且有成熟的调试工具链。

---

## 五、结论：与 VirtualApp 的关系

| 维度 | 结论 |
| --- | --- |
| **代码派生** | ❌ **无**。VA 的 8 个代码级标识全部 0 |
| **命名参考** | ✅ 有。`VAMS`/`VPMS` 缩写沿用了 VA 家族的习惯 |
| **架构** | ❌ 不同。VA 是 `V` 前缀 + `VirtualCore` 单入口；NEXTVM 是 `Virtual*` 全称 + `VirtualEngine` 门面 + `StubRegistry`/`processSlot` |
| **代码来源** | ✅ **AOSP 16 框架代码移植**（`ApkLiteParseUtils` 等有 AOSP 版权头） |
| **许可** | ✅ Apache-2.0，与本项目要求一致 |

**对本项目约束的影响**：

> 项目要求排除 VirtualApp 家族（"该公司喜欢无理威胁开源社区"）。
> 经上述验证，**ShitJumpingVM/NEXTVM 不属于 VA 家族**——它没有 VA 的代码，
> 只借用了 `VAMS/VPMS` 的命名习惯，实现是独立的（基于 AOSP 16 + 自有 hook 层）。
> **约束未被违反。**

---

## 六、对"为什么它比我们手写的好"的一点解释

从特征片段能看出上游的设计取向：

| 特征 | 含义 |
| --- | --- |
| `Phase 2+ architecture` | 分阶段重构过，不是一次性写完 |
| 初始化顺序 10 步（`bypassHiddenApis` → … → `loadInstalledApps`） | 顺序被显式文档化，说明踩过依赖顺序的坑 |
| `ApkLiteParseUtils` 移植 | 直接用 AOSP 的解析器，而不是自己写 APK 解析 |
| `35 个系统服务代理` | 覆盖面靠"数量"堆出来 |
| `AntiDetectionEngine` / `IdentitySpoofingEngine` | 把"对抗检测"当成一等公民模块 |
| `GuestBreadcrumb`（199 次） | 有贯穿全链路的调试埋点 |

这些正好解释了我们这几天遇到的两类问题：
- **检测面**（`P13`/`P16` 暴露）：上游有 `AntiDetectionEngine`，但显然没覆盖 PackageManager 宿主包这一项
- **调试困难**：`GuestBreadcrumb` 这类埋点我们没充分利用
