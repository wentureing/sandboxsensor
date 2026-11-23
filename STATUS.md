# 项目状态（STATUS）

> 最后更新：**2026-11-23**
> 本文件是唯一「当前状态」入口。历史细节见 `docs/records/`，调研见 `docs/01`–`docs/07`，计划见 `docs/计划-提升路线图.md`。

---

## 0. 开工前必读

### 0.1 排查顺序（本项目已绕弯路的教训）

> **guest 行为异常但没崩溃/没栈时，先看 `GuestBreadcrumb`**（logcat tag = `BootCrumb`），
> 再看探针 P/N 系列，**最后**才看 native 栈。详见 `AGENTS.md` 与 `docs/调试指南.md`。

上游 NEXTVM 已埋 **199 处** breadcrumb，记录有序、带时间戳、含被吞异常的完整栈。

**本项目三次弯路都是因为跳过它、从结果倒推**：

| 弯路 | 错误归因 | 真实原因 |
| --- | --- | --- |
| RenderThread SIGABRT | Adreno 驱动缺陷 | 容器 native hook 污染 GPU 驱动 |
| 长按 ANR | `WindowManager`/`PopupWindow` | 只在 WebView 里出现（isolated 进程） |
| Via 主页源码 | MIME / WebView 库未 patch / 沙箱该放外部 | WebView 渲染在 isolated 进程 |

### 0.2 已确认的边界（**不要再重复调查**）

| # | 结论 | 记录 |
| --- | --- | --- |
| 1 | **RenderThread SIGABRT 是容器 native hook 污染 GPU 驱动**，不是 Adreno 缺陷 | `记录-native-hook作用域与污染.md` |
| 2 | hook 作用域：**容器自身 + 图形栈永不 patch**；系统库默认不 patch（`NS_HOOK_SYSTEM_LIBS=0`） | 同上 |
| 3 | **WebView 渲染在 `webview_zygote` 派生的 isolated 进程**，容器 hook 覆盖不到 | `记录-WebView问题全链路.md` |
| 4 | 因此 patch `libwebviewchromium*` **无效**（p17 已回退，分支 `backup-webview-hook`） | 同上 |
| 5 | 容器内**文字选择完全正常**（复制/全选/搜索/分享 都显示）——问题只在 WebView | 同上 |
| 6 | **Gaia 沙箱在 `/data/user/0/<容器>/gaia/`（内部存储）**，不是 `/sdcard` | `记录-Gaia机制分析.md` |
| 7 | Gaia 做到了「maps 无宿主痕迹」（`hostHits=0`），我们 `pkg_hit=13` | 同上 |
| 8 | 容器不属于 VirtualApp 家族（VA 标识 0 命中），Apache-2.0，上游 = TanvirHossain2/NEXTVM | `记录-上游溯源.md` |
| 9 | **两家沙箱根并存**：`app_virtual/`（`SandboxManager`）vs `files/virtual/`（实际使用）——上游 bug | `记录-双轨沙箱路径诊断.md` |
| 10 | 容器内 adb **读不到** Gaia 沙箱；观察必须靠「在容器内运行的探针」 | `记录-Gaia机制分析.md` |

### 0.3 进行中的计划

见 **`docs/计划-提升路线图.md`**（P0/P1/P2 完整设计 + 验收标准）。

```
P0-1 DiagnosticsSink 接口 + NoOp          ✅ 完成 a7650f2
P0-2 probe-out + CmdReceiver 三命令       ✅ 完成 a7650f2（COLLECT/LIST 实测通过，CAT_OUT 待 p27+ 验证）
P0-3 进程分割 + 删除路径豁免               ⏭ 下一步（p18 的 /sdcard/Download 豁免从根上无效）
P1-2 HostReadPolicy + 17 条纯 JUnit       ✅ 完成 0cef67f（p26 CI 全绿）
P1-1 :core:diagnostics + SelfCheckRunner  待 P0-3 之后
P1-3 WebView 宿主代读最小版                需先验证 Gaia 机制（探针 v1.8）
```

**下一步应该是**：落盘 `docs/changes/20261123-*.md` 修改计划 → 等确认 → 编码。

---

## 1. 两个仓库

| 仓库 | 角色 | 说明 |
| --- | --- | --- |
| `wentureing/sandboxsensor` | **主仓库**：探针 + 文档 + 全部 CI | 所有 workflow 都挂这里 |
| `wentureing/ShitJumpingVM` | 容器骨架（fork） | 只放代码，**不含 CI** |

本地副本（`_ref/`，已 gitignore）：

```
_ref/ShitJumpingVM   容器源码（origin = wentureing/ShitJumpingVM）
_ref/NestSpace       摘件来源（Apache-2.0）
_ref/NEXTVM          上游快照（仅溯源）
_ref/android-vm      自研 DEX 运行时（边界案例）
_ref/aosp/raw        AOSP 官方文档归档
```

---

## 2. CI

| workflow | 触发 | 产物 |
| --- | --- | --- |
| `build-probe` | push（`probe/**`、workflow 自身）+ 手动 | `probe-<7位sha>-<abi>.apk` |
| `build-container` | push（仅 workflow 自身）+ 手动 | `container-shitjumpingvm-<7位sha>.apk` |

**产物形态**：Release tag `apk-latest`，下载即 `.apk` 原文件，**不经过 artifact zip**。
**Release 只保留最新一组**（`tools/release/gh-clean-release.mjs`）。

---

## 3. 探针（`com.sandbox.probe` **v1.7** / `versionCode 8`）

| 项 | 值 |
| --- | --- |
| 条目 | **74+**（含 `procstat`/`maps`/`soload`/`crosspkg`/`prop`/`fd`/`mount`/`uiprobe` 等分组） |
| 主入口 | **`UiTestActivity`**（LAUNCHER；显示纯文字页 + 后台跑 `ProbeRunner`） |
| 备用入口 | `ProbeActivity`（被容器拉起时不可控，保留） |
| 输出通道 | ① logcat 分块 base64（`REPORT v1 part n/m`，**主通道**）② `files/probe-report.json` ③ `getExternalFilesDir` 副通道（**容器内会落到沙箱镜像，adb 拉不到**） |
| 导入容器后 | 需 `COLLECT_OUT` 才能把报告取出来（P0-2 计划中） |

**基线**：直接安装的 74 项完整报告见 `probe-reports/report-*.json`；
Gaia 内运行的对照见 `记录-Gaia机制分析.md`。

---

## 4. 容器现状（**p19**）

### 4.1 已打补丁

| # | 补丁 | 内容 |
| --- | --- | --- |
| p13/p15 | hook 作用域重写 | 容器自身 + 图形栈永不 patch |
| p16 | `CmdReceiver` 恢复 | `INSTALL` / `LAUNCH` / `LAUNCH_ACTIVITY` / `UNINSTALL` / `LIST` |
| p17 | WebView 白名单 | **已回退**（无效），分支 `backup-webview-hook` |
| p18 | 导入只读豁免 | `/sdcard/Download/`、`/storage/emulated/0/Download/`、`/data/local/tmp/` |
| p19 | 回退 p17 | 导入豁免保留 |

### 4.2 已知缺陷（按优先级）

| 优先级 | 缺陷 | 状态 |
| --- | --- | --- |
| P0 | WebView 本地页在容器内不可用（isolated 进程） | ❌ 未解，P1-3 计划中 |
| P1 | `P13` 宿主包可见、`P16` 宿主 data 可 stat | ❌ 未修 |
| P1 | maps 暴露宿主包名（`N10 pkg_hit=13`） | ❌ 未修（Gaia 已做到 `hostHits=0`） |
| P2 | 两家沙箱根并存（`app_virtual` vs `files/virtual`） | ❌ 上游 bug，P2-1 计划中 |
| P2 | 未声明 `READ_MEDIA_*`（Android 13+） | ❌ 未做 |

---

## 5. 已知环境事实

| 项 | 值 |
| --- | --- |
| 设备 | vivo V2463A，`10AFA8169Z0039G`，Android 15 / API 35，arm64-v8a |
| page size | **4096**（不是 16KB 设备） |
| 设备时钟 | 比电脑**快约 2 分钟** |
| logcat | 必须用 `-b all`；`-c` 清空后短时间内可能为空 |
| 后台监听 | `detached` 进程活不过一次工具调用 → **操作后立刻 `logcat -d -b all`** |
| adb 读容器私有目录 | 只有 `run-as com.nextvm.app` 可用；`/sdcard/Android/data/<容器>/` 在 vivo 上被拒 |
| adb 读 Gaia 沙箱 | **完全被拒**（只能靠容器内探针） |

---

## 6. 常用命令

```powershell
# 安装 guest（p18 起可直读 /sdcard/Download）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path /sdcard/Download/probe.apk

# 启动（LAUNCHER）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg com.sandbox.probe

# 启动指定 Activity（容器只解析 LAUNCHER，非 LAUNCHER 页面必须用这条）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH_ACTIVITY `
    --es pkg com.sandbox.probe --es activity com.sandbox.probe.UiTestActivity

# 看命令结果
adb logcat -d -s CmdReceiver

# 采集（操作后立刻 dump，不要 -c）
adb logcat -d -b all -v threadtime > probe-reports/dump-$(Get-Date -f HHmmss).txt

# 提取报告
python tools/probe/extract-report.py probe-reports/dump-*.txt

# Gaia 全流程自动化
.\tools/device/gaia-test.ps1
```
