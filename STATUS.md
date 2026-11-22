# 项目状态（STATUS）

> 最后更新：2026-11-22
> 本文件是唯一"当前状态"入口，历史细节见 `records/` 与 `01`–`07` 调研文档。

---

## 1. 两个仓库

| 仓库 | 角色 | 说明 |
| --- | --- | --- |
| [`wentureing/sandboxsensor`](https://github.com/wentureing/sandboxsensor) | **主仓库**：探针 + 文档 + 全部 CI | 所有 workflow 都挂在这里 |
| [`wentureing/ShitJumpingVM`](https://github.com/wentureing/ShitJumpingVM) | 容器骨架（fork） | 只放代码，**不含 CI**（已移除自带 android-ci.yml） |

本地副本（`_ref/`，已 gitignore）：

```
_ref/ShitJumpingVM   容器源码（独立 git 仓库，origin = wentureing/ShitJumpingVM）
_ref/NestSpace       摘件来源（Apache-2.0）
_ref/NEXTVM          ShitJumpingVM 上游快照（仅溯源）
_ref/android-vm      自研 DEX 运行时（边界案例）
_ref/aosp/raw        AOSP 官方文档归档（15+ 页面）
```

---

## 2. CI（全部在 sandboxsensor）

| workflow | 触发 | 产物 |
| --- | --- | --- |
| `build-probe` | push（`probe/**`、workflow 自身）+ 手动 | `probe-<7位sha>-<abi>.apk` |
| `build-container` | push（仅 workflow 自身）+ 手动 | `container-shitjumpingvm-<7位sha>.apk` |

**产物形态**：上传到 Release（tag `apk-latest`，`overwrite_files`），下载即 `.apk` 原文件，**不经过 artifact zip**。

固定缓存键（bump `CACHE_EPOCH` 才失效）：

```
sdk-android-35-bt35.0.0-ndk27.1.12297006-cmake3.22.1-v1
gradle-home-8.9-v1
```

---

## 3. 探针（com.sandbox.probe v1.1）

| 项 | 值 |
| --- | --- |
| 条目 | **51 条**（Java 39 + native 15，含 `N13–N15` 的 `/data/user/0` 前缀用例） |
| 主入口 | `ProbeProvider.onCreate`（起后台线程跑全量探针，**不依赖 Activity**） |
| 备用入口 | `ProbeActivity`（`NoActionBar` + 系统 icon，仅调试） |
| 输出通道 | ① `files/probe-report.json`（`run-as` 可拉）② logcat 分块 base64（`REPORT v1 part n/m`）③ `flushEarly` 的 `EARLY v1` 分块 |
| 版本 | `versionCode 2` / `versionName 1.1` |

T1 golden 基线（Android 15 / vivo V2463A）：`records/installed-V2463A-150347.md`（57 项全 ok）。

---

## 4. 容器现状（ShitJumpingVM + 补丁 p1）

已打补丁（提交 `a54c806`）：

1. **止血**：替换 `ActivityInfo` 时把 `icon/logo/banner/roundIcon` 归零
   → 修复 `Resources$NotFoundException: Resource ID 0x7f0a0000`
2. **命令行入口**：新增 `CmdReceiver`（`exported`），支持 install/launch/uninstall

实测已确认**能跑通**的链路：

```
导入 APK → ClassLoader 替换 → LoadedApk 重建 → Provider 初始化 →
Application.onCreate → （卡在 Activity 资源绑定，p1 后待验证）
```

容器已知缺陷（按优先级）：

| 优先级 | 缺陷 | 状态 |
| --- | --- | --- |
| P0 | `ActivityInfo` 资源 ID 未重映射（icon/logo/banner/roundIcon） | ✅ p1 已止血 |
| P0 | `getResources()` 抛异常（breadcrumb `getResources()=THREW`） | ❌ 未修，需 root+resolver 重构 |
| P1 | 主进程 RenderThread 在 vivo Adreno 驱动里 SIGABRT（Compose UI 渲染） | ❌ 未修，偶发 |
| P2 | `SELinux avc denied { link }`（v81-apk-watch） | ❌ 未修 |

---

## 5. 已知环境事实（实测）

| 项 | 值 |
| --- | --- |
| 设备 | vivo V2463A，`10AFA8169Z0039G`，Android 15 / API 35，arm64-v8a |
| page size | **4096**（不是 16KB 设备，16KB 路径无法验证） |
| 设备时钟 | 比电脑**快约 2 分钟** |
| logcat | **可用**，但必须用 `-b all`；`-c` 清空后短时间内可能为空 |
| 后台监听 | `detached` 进程活不过一次工具调用 → **统一改为"操作后立刻 `logcat -d -b all`"** |
| adb 权限 | `workspace-write` 沙箱下可直接用，无需完全权限 |

---

## 6. 待办（按顺序）

1. **验证 p1**：装新容器 → `CmdReceiver` 安装/启动探针 → 确认 `Activity.onCreate` 后不再抛 `Resources$NotFoundException`
2. **拿到 T2 完整数据**：探针 v1.1 走 Provider 入口，应能跑完 51 条 → 与 T1 做 `diff-report`
3. **自动化脚本**：`tools/auto-test.ps1`（安装→运行→采集→自动结束，无后台进程）
4. 若 T2 通过 → 按 `01 §5` 决策表决定是否引入 B1 路径层

---

## 7. 常用命令

```powershell
# 构建产物下载（Release 直链，非 zip）
# https://github.com/wentureing/sandboxsensor/releases/download/apk-latest/<file>.apk

# 容器命令行驱动
adb shell mkdir -p /sdcard/Android/data/com.nextvm.app/files/inbox
adb push probe.apk /sdcard/Android/data/com.nextvm.app/files/inbox/probe.apk
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path /sdcard/Android/data/com.nextvm.app/files/inbox/probe.apk
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH  --es pkg com.sandbox.probe

# 采集（操作后立刻 dump）
adb logcat -d -b all -v threadtime > probe-reports/dump-$(Get-Date -f HHmmss).txt

# 生成可读记录 / 对比
python tools/make-record.py probe-reports/report-installed-*.json
python tools/diff-report.py probe-reports/report-installed-*.json probe-reports/report-container-*.json
```
