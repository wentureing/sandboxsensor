# sandbox-probe

**探针工程（独立）**：验证 Android 平台在「应用级虚拟化容器」场景下的真实行为。

- 本仓库只含探针 APK + 调研文档，**不包含容器代码**。
- 容器骨架在另一个仓库：[`wentureing/ShitJumpingVM`](https://github.com/wentureing/ShitJumpingVM)（fork 自 NEXTVM）。
  本地参考副本在 `_ref/ShitJumpingVM`（已 gitignore，仅作骨架阅读/移植来源）。
- 探针与容器**不共享** Gradle 配置：探针用 `AGP 8.7.3 / Gradle 8.9 / NDK 27.1.12297006 / compileSdk 35`，
  容器用同一套版本，但各自独立 `settings.gradle.kts`，避免互相拖累构建。

## 目录

```
.
├── 01..07-*.md              调研与设计文档（决策依据）
├── probe/                   探针 APK（com.sandbox.probe）
│   ├── build.gradle.kts
│   └── src/main/{AndroidManifest.xml, java/, cpp/}
├── tools/
│   ├── collect-probe.ps1    adb 采集：重组 logcat 分块 → report.json
│   ├── diff-report.py       T1/T2 报告差异对比
│   ├── fetch-aosp.mjs       抓取 AOSP 文档（走 12450 代理）
│   └── html2txt.py          HTML → txt
├── _ref/                    上游仓库副本与归档（gitignore）
└── .github/workflows/probe-build.yml
```

## 构建：完全在 GitHub Actions（不在本地编译）

探针是**显式任务**，`settings.gradle.kts` 只 include `:probe`，构建入口只有：

```bash
gradle :probe:assembleDebug            # 本地（如装了 Gradle）
```

Actions：`Actions → build-probe → Run workflow`，产物 `probe-debug-apk`（arm64-v8a / 可选 x86_64）。

### 固定缓存键

| 缓存 | 固定 key |
| --- | --- |
| Android SDK（platforms/build-tools/ndk/cmake/platform-tools） | `sdk-android-35-bt35.0.0-ndk27.1.12297006-cmake3.22.1-v1` |
| Gradle home（`~/.gradle/caches`） | `gradle-home-8.9-v1` |

失效方式：改 workflow 里的 `CACHE_EPOCH`（`v1` → `v2`），两处同时生效。
SDK 自建于 `$GITHUB_WORKSPACE/.android-sdk`（runner 用户可写，避开 `/usr/local` 权限问题），已在 `.gitignore`。

## 探针怎么跑（安装由你手动完成）

### 关键设计

1. **主通道是 logcat，不是文件**：报告 base64 分块输出到 `logcat -s PROBE`。
   因此**包被卸载后仍能采集**——这是 T2 能成立的前提。
2. **同一个 APK 跑两遍**：T1（直接安装）建 golden 基线，T2（容器内）与之逐项 diff。
   没有 T1，「容器返回值异常」和「平台本来就这样」无法区分。
3. 零第三方依赖、零敏感权限，保证两种场景可比。

### T0 环境基线

```powershell
adb devices
adb shell getprop ro.build.version.release   # 期望 15
adb shell getconf PAGESIZE
```

### T1 golden 基线（手动安装）

```powershell
adb install -r -t .\probe-debug.apk
.\tools\collect-probe.ps1 -Mode installed
```

### T2 容器内

```powershell
# 1) 卸载探针，确保 /data/data/com.sandbox.probe 不存在（否则字面量路径测试会失真）
adb uninstall com.sandbox.probe

# 2) 把 APK 推到设备，在容器里【手动导入 APK】并点开探针图标
adb push .\probe-debug.apk /sdcard/Download/

# 3) 只采集，不安装
.\tools\collect-probe.ps1 -Mode container
```

> `collect-probe.ps1` 默认**不安装**；只有显式 `-Install -Apk <path>` 才会代劳。

### 对比

```powershell
python .\tools\diff-report.py probe-reports\report-installed-*.json probe-reports\report-container-*.json
```

## 探针覆盖范围

| 组 | 内容 |
| --- | --- |
| `P01–P06` | Provider 阶段路径快照（验证承接时机：Provider 早于 Application.onCreate） |
| `J01–J07` | `Context` 各目录 + SQLite |
| `J08–J13` | 外部存储 / OBB / `Environment` |
| `J14–J18` | `ApplicationInfo`（dataDir/sourceDir/nativeLibraryDir/split/deviceProtected） |
| `J19–J21` | `PackageManager` |
| `J22–J25` | 身份（UID/PID/Build/SELinux） |
| `J26–J29` | **字面量路径** `/data/data/<pkg>`、`/data/user/0/<pkg>`（L1 关键用例） |
| `J30–J31` | `getCanonicalPath()`（含不存在路径） |
| `J32–J34` | `java.io` / `SharedPreferences` |
| `N01–N12` | **libc**：open/stat/access/fopen/**openat 相对路径**/readlink fd/**syscall**/maps 扫描/写操作 |

## 已知限制

- 默认只构建 `arm64-v8a`；模拟器需在触发时选 `x86_64` 或 `universal`。
- `N10.maps_scan` 在 P2 阶段之前**预期**能看到真实路径，不算失败，只做记录。
- 采集脚本需要本机有 `adb`（唯一需要本地工具的场景；编译仍在 Actions）。
- 探针代码尚未经过编译器验证（首轮 Actions 构建可能需修小问题）。
