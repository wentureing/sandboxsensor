# Gaia(AppHider) vs 我们的容器：探针 v1.3 实测对比

- 日期：2026-11-22 19:41
- 探针：v1.3（`versionCode=4`，74 项，含外部存储副通道）
- 设备：`HES8ZDUWSW4T7TDQ`（PEAT00）
- 数据（**均从外部存储直接 pull，无需 root/run-as**）：
  - 容器内：`/sdcard/Android/data/com.app.hider.master.pro.cn/gaia/com.sandbox.probe/Android/data/com.sandbox.probe/files/probe-report.json`（12,960 B）
  - 直接安装：`/sdcard/Android/data/com.sandbox.probe/files/probe-report.json`（11,669 B）

---

## 一、外部副通道验证成功 ✅

```
container: mode=container:dir-other(/data/user/0/com.app.hid...)  pkg=com.sandbox.probe  n=74
installed: mode=installed                                        pkg=com.sandbox.probe  n=74
```

两份都完整（74 项），且**都能用 `adb pull` 直接取** —— 这条改动解决了「release 容器不可 run-as」的取数难题。

---

## 二、逐项对比

| 探针 | Gaia 容器 | 直接安装 | 我们的容器（p15） | 评价 |
| --- | --- | --- | --- | --- |
| `P01` Uid | **10012**（= 容器 uid） | 10478 | 10308（= 宿主 uid） | **两者都暴露** |
| `P02` cmdline | `com.sandbox.probe` | 同 | `com.sandbox.probe` | Gaia 也改了进程名 ✅ |
| `P03` 同 UID 进程数 | **1** | 1 | **2** | **Gaia 更干净** |
| `P04` 线程数 | 39 | 41 | 31 | — |
| `P05` maps hostHits | **0** | 0 | **0（Java）/ 13（native N10）** | Gaia 的 maps 更干净 |
| `P09` findLibrary | **`/data/user/0/com.app.hider.master.pro.cn/gaia/data/app/com.sandbox.probe/base.apk!/lib/arm64-v8a/libprobe.so`** | `/data/app/~~PNAk...` | `.../files/virtual/data/<inst>/lib/libprobe.so` | **Gaia 路径含 guest 包名** |
| `P11` nativeLibraryDir | **`.../gaia/data/app/com.sandbox.probe/lib/arm64`** | `/data/app/.../lib/arm64` | `.../files/virtual/data/<inst>/lib` | 同上 |
| `P12` System.load(abs) | `libprobe.so missing in .../gaia/data/app/.../lib/arm64` | missing（APK 内嵌） | **loaded 成功** | Gaia 的 SO 在 APK 内，未解压 |
| **`P13` 宿主包可见** | **`hidden: NameNotFoundException`** | hidden | **`visible`** ❌ | **Gaia 隐藏成功，我们没有** |
| `P14` installedPackages | **3** | 164 | 1 | Gaia 暴露 3 个（白名单），我们 1 个（更严格） |
| `P15` `/data/data` 可列 | `false` | false | false | 一致 |
| **`P16` 宿主 data 目录** | **`exists=false`** | false | **`exists=true canRead=true`** ❌ | **Gaia 隐藏成功，我们泄漏** |
| `P17` java.class.path | `.` | `.` | `.` | 都干净 |
| `P20` fd count | 132 | 132 | 165 | — |
| `P21` fd targets | `/dev/null` + `/proc/<pid>/fd/N` | 同 | 同 | — |

---

## 三、Gaia 做得比我们好的三点（重要启示）

### ① 路径里嵌入 **guest 包名**

```
Gaia :  /data/user/0/com.app.hider.master.pro.cn/gaia/data/app/com.sandbox.probe/base.apk
         └──────────── 容器包名 ────────────┘ └引擎┘└──── guest 包名 ────┘
我们 :  /data/user/0/com.nextvm.app/files/virtual/apks/com.sandbox.probe_1795346395414.apk
         └──────── 容器包名 ────────┘└───── 无 guest 包名语义 ─────┘
```

- Gaia 的路径结构是 `容器/gaia/data/app/<guest_pkg>/`，**guest 包名作为真实目录名存在**
- 我们的是 `files/virtual/apks/<instance_id>.apk`，**看不出 guest 包名**

**这正是 B1 想达成的效果**（虽然 Gaia 前缀仍是容器包名，还不完美，但比我们好）。

### ② 隐藏宿主包与宿主 data 目录

| 项 | Gaia | 我们 |
| --- | --- | --- |
| `getPackageInfo("com.app.hider.master.pro.cn")` | **NameNotFoundException** | **visible** |
| `stat("/data/data/com.app.hider.master.pro.cn")` | **ENOENT** | **exists=true canRead=true** |

这两项是我们容器的**明确缺陷**，Gaia 已经做对了。改动成本低（PackageManager 代理 + stat hook）。

### ③ 同 UID 进程数控制到 1

Gaia 的 `P03 count=1`，我们是 2（host 主进程 + guest 进程）。

- 可能 Gaia 的 host 主进程在 guest 启动后退出了，或 guest 直接在 host 进程里跑
- 这是 `ps | grep <uid>` 检测的关键指标，我们为 2（未达阈值 >4，但仍比 1 差）

---

## 四、Gaia 的弱点（我们可以做得更好的地方）

| 项 | Gaia | 说明 |
| --- | --- | --- |
| `P01` Uid = 容器的 uid（10012） | 暴露 | guest 能直接看到 `getuid()` 返回容器 uid 而非"自己的" |
| `P09`/`P11` 路径前缀含 `com.app.hider.master.pro.cn` | 暴露 | 一个字符串包含检测就能判定 |
| `P14` installedPackages = 3 | 暴露 3 个包 | 我们只暴露 1 个（更严格） |
| `P12` SO 在 APK 内未解压 | `nativeLibraryDir` 空 | 我们解压了（`P12 loaded 成功`） |

---

## 五、行动建议（按收益/成本排序）

| # | 动作 | 收益 | 成本 |
| --- | --- | --- | --- |
| 1 | **修 `P13`/`P16`**：隐藏宿主包与宿主 data（`getPackageInfo` 返回 NameNotFound、`stat` 返回 ENOENT） | 消掉 2 个 P0 泄漏 | 低 |
| 2 | **路径改为含 guest 包名**（`files/virtual/<guest_pkg>/...` 或 `/data/user/0/<容器>/gaia/data/app/<guest_pkg>/`） | `P09`/`P11` 看起来合理；为 B1 铺路 | 中 |
| 3 | 修 `J19`(uid=0) / `J20`(targetSdk=0) / `J18`(deviceProtectedDataDir) | 消掉 3 个元数据异常 | 低 |
| 4 | maps 伪造覆盖 native 侧（`N10 pkg_hit=13 → 0`） | 关掉最强检测点 | 高 |

---

## 六、附：`INSTALL_FAILED_UPDATE_INCOMPATIBLE` 的原因

```
Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package com.sandbox.probe
  signatures do not match previously installed version; ignoring!]
```

**原因**：CI 每次构建用**随机生成的 debug keystore**。GitHub Actions runner 是干净环境，`~/.android/debug.keystore` 不存在 → Gradle 自动生成一个新的 → **每次构建签名都不同** → 无法覆盖安装。

**表现**：`adb install` 拒绝 → 必须先 `adb uninstall`。

**根治**：在 `build-probe.yml` / `build-container.yml` 里缓存 `~/.android/debug.keystore`：

```yaml
- name: Restore debug keystore
  uses: actions/cache@v4
  with:
    path: ~/.android/debug.keystore
    key: android-debug-keystore-v1

- name: Ensure debug keystore
  run: |
    mkdir -p ~/.android
    [ -f ~/.android/debug.keystore ] || keytool -genkey -v \
      -keystore ~/.android/debug.keystore -storepass android -alias androiddebugkey \
      -keypass android -keyalg RSA -keysize 2048 -validity 10000 \
      -dname "CN=Android Debug,O=Android,C=US"
```

加上之后，同一 `key` 期间的所有构建签名一致，可以 `install -r` 覆盖安装。
