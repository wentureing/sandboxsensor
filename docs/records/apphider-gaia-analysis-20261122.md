# 第三方容器 AppHider (Gaia) 拆解 + 探针版本核实

- 日期：2026-11-22 19:2x
- 设备：`HES8ZDUWSW4T7TDQ`（PEAT00 / OPPO 系）
- 容器：`com.app.hider.master.pro.cn`（App Hider，**Gaia 引擎**）

---

## 一、探针版本核实（更正之前的说法）

### 实测

| 项 | 值 |
| --- | --- |
| 设备上 APK 路径 | `/data/app/~~jjEJeex-N-ADHwFDL_F4mA==/com.sandbox.probe-Gv8F-okQ11pmcblDglHftQ==/base.apk` |
| 设备上 APK MD5 | `c112030844a6a1e6aa7313b73f56ada4` |
| `dist/probe-4bba18c-arm64-v8a.apk` MD5 | **`c112030844a6a1e6aa7313b73f56ada4`（完全一致）** |
| versionCode / versionName | **3 / 1.2** |
| firstInstallTime / lastUpdateTime | 2026-11-22 19:25:47（两者相同 → 只装过一次） |
| 报告生成时间 | 19:29 |

**结论**：设备上跑的就是 `4bba18c`，**含那两个统计 bug 的修复**。

### 关于"输出不同是否因为 bug 修复"的严谨结论

**不能简单说"版本不同导致"**——两次运行的 `versionCode` 都是 **3**、`versionName` 都是 **1.2**：

| 数据 | 运行时间 | 版本号 | `P03` | `P05 lines` |
| --- | --- | --- | --- | --- |
| 老设备（`10AFA8169Z0039G`） | 19:14 | 3 / 1.2 | **0** | **32** |
| 新设备（`HES8ZDUWSW4T7TDQ`） | 19:29 | 3 / 1.2 | **1** | **2513** |

→ **版本号相同，所以"版本差异"解释不了输出差异。**

唯一变量是 commit：

| commit | 内容 | 对应行为 |
| --- | --- | --- |
| `60fbcb1` | 修 `findLibrary` 反射调用（编译期问题） | 不影响 P03/P05 |
| **`4bba18c`** | **① `readText` 改循环读；② `P03` 改按空白分割匹配** | **正好对应 `P05 lines` 和 `P03 count`** |

**所以**：
- ✅ `P03 0→1`、`P05 32→2513` **确实是 `4bba18c` 两个修复的效果**
- ⚠️ 但严格说这是「同 versionCode、不同 commit」的对比，不是受控实验；如果要 100% 归因，需要把旧构建装到新设备上复现一次
- ❌ 不能说成"版本不同导致"——版本号是相同的，这点我之前的表述不准确

---

## 二、容器拆解：AppHider / Gaia

### APK 结构（27.1 MB，2003 个条目）

| 库 | 大小 | 作用 |
| --- | --- | --- |
| `libgaianative_64.so` | 358,624 | **Gaia 引擎核心**（`com/prism/gaia/client/natives/NativeMirror`） |
| `libgaianative.so` | 214,688 | 32 位版 |
| `libhelper.so` | 149,584 | `callGaiaApiPreInit` 预初始化入口 |
| `libzeus_direct_dex.so` | 14,160 | dex 直载 |
| `libBugly-ext.so` / `libbeaconid.so` / `libqmp.so` / `libpangleflipped.so` | — | Bugly / 腾讯灯塔 / 广告 SDK |

**dex 内类名线索**：
- `gaia/client/stub/GuestInitializerProvider`
- `gaia/naked/metadata/android/app/ActivityThread`
- `gaia/naked/metadata/com/android/internal/...`
- `com/prism/gaia/client/core/NativeWrapper`

→ **Gaia 在 Java 层内置了一套 "naked metadata"（重写版 framework 类元数据）+ Naked 风格 hook**，这与我们的 `ActivityThreadHook` 思路同源。

**调试路径泄漏**（有意思）：
```
/Users/quanzhao/git-space/AppHider-Gaia/AppHider/tools/jni/fb/jni/Environment.cpp
```
→ 开发者 `quanzhao`，用了 facebook 的 `fbjni`。

### 沙箱布局（关键发现：**在外部存储**）

```
/sdcard/Android/data/com.app.hider.master.pro.cn/
├── files/apk, files/res, files/Download (pangle 广告)
├── cache/pangle_*
└── gaia/                          ← 沙箱根
    ├── com.sandbox.probe/
    │   └── Android/
    │       ├── data/com.sandbox.probe/{files,cache}   ← 空
    │       ├── media/com.sandbox.probe/
    │       └── obb/com.sandbox.probe/
    ├── mark.via/Android/data/mark.via/files/tabs/00-index.json
    └── org.eu.cocomi.pxvek/Android/...
```

**重要**：Gaia 把 guest 的 **外部存储**（`/sdcard/Android/data/<pkg>`、`media`、`obb`）映射到 `gaia/<pkg>/Android/...`，这部分**完全可读**。

### 为什么拿不到探针报告

| 事实 | 说明 |
| --- | --- |
| `gaia/com.sandbox.probe/Android/data/com.sandbox.probe/files/` **是空的** | 目录创建时间 `2026-11-22 19:26`，但没有任何文件 |
| 内部数据 `/data/data/com.app.hider.master.pro.cn/` **Permission denied** | 容器是 release 版，不可 `run-as`；`adb shell` 也无权 |
| logcat 里两张报告都是 `mode=installed` | 跑的是**直接安装**的那个实例（uid 10477），不是容器内 |
| 日志里没有 guest 进程（`com.app.hider.master.pro.cn:p0` 之类） | 容器**只启动了 `launcher3.icon_origin`（图标 Activity）**，没有真正启动 guest |

**结论：探针在 Gaia 容器内从未成功运行，因此不存在「容器内的探针报告」。**

沙箱目录 19:26 被创建，说明容器**尝试过**建立 guest 环境，但 guest 进程没起来（或起来后立刻死掉，没走到 `Report.flush()`）。

---

## 三、要拿到容器内数据，还缺什么

| 障碍 | 现状 | 可能出路 |
| --- | --- | --- |
| 内部数据不可读 | release 版 + 无 root | ① 找 Gaia 是否把内部数据也镜像到 `/sdcard`（目前**没有**）<br>② 用 `adb backup`（Android 12+ 基本失效）<br>③ 设备有 `brevent`（旧设备上有，新设备待确认），但需 root |
| guest 进程没起来 | 容器 UI 只到图标 Activity | 需要在 Gaia UI 里**正确添加并启动** guest；当前操作路径可能不对 |
| 探针不写外部 | `Report.flush()` 写 `ctx.getFilesDir()` → 内部路径 | 可以让探针**额外**写一份到 `getExternalFilesDir()`（沙箱映射到 `gaia/<pkg>/Android/data/<pkg>/files/`，**可读**） |

### 建议：给探针加「外部存储副通道」

这是**成本最低、立刻可用**的改动：

```java
// Report.flush() 里，除了 files/probe-report.json，
// 再写一份到 getExternalFilesDir(null)/probe-report.json
File ext = ctx.getExternalFilesDir(null);
if (ext != null && ext.canWrite()) {
    write(new File(ext, "probe-report.json"), json);
}
```

对 Gaia 而言，落点就是：
```
/sdcard/Android/data/com.app.hider.master.pro.cn/gaia/com.sandbox.probe/Android/data/com.sandbox.probe/files/probe-report.json
```
→ **`adb pull` 直接可拿**。对我们自己的容器（`com.nextvm.app`）同理。

---

## 四、当前可用的数据

| 文件 | 内容 |
| --- | --- |
| `probe-reports/hiderA-192814.json` | **容器外基线**（79 项，`mode=installed`，uid 10477） |
| `probe-reports/hiderB-192814.txt` | logcat（4.25 MB，18 个 REPORT 分块，均容器外） |
| `probe-reports/hider-dump/hider-base.apk` | Gaia 容器 APK（27.1 MB，已拆解分析） |

**容器内：无数据。**
