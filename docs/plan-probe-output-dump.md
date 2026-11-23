# 设计：探针输出落盘 + CmdReceiver 导出

- 日期：2026-11-23
- 来源：用户明确的两条设计
- 状态：**设计文档，待编码**
- **已核对：沙箱真实根目录 + 发现两套目录并存（见 §0）**

---

## 零、核对结果：沙箱真实根目录

### 0.1 结论

| 路径 | 谁在用 | 实际内容 | 判定 |
| --- | --- | --- | --- |
| **`/data/user/0/com.nextvm.app/files/virtual/`** | `VirtualEngine.kt:163/942/1285`<br>`PackageManagerProxy.kt:645/717` | `data/<instance>/` 有 **12 项**（files/cache/databases/lib/shared_prefs/sdcard/…） | ✅ **真实沙箱根** |
| `/data/user/0/com.nextvm.app/app_virtual/` | `AppModule.provideSandboxManager`（`context.getDir("virtual")`） | `data/<instance>/` 只有 **1 项**（空的 `sdcard`） | ❌ **空壳，`SandboxManager` 用错了**

### 0.2 代码证据

```kotlin
// ✅ VirtualEngine.kt:163（真实）
virtualRoot = File(context.filesDir, VirtualConstants.VIRTUAL_DIR)   // files/virtual
// ✅ PackageManagerProxy.kt:717（guest 看到的 dataDir）
dataDir = "${context.filesDir}/${VirtualConstants.VIRTUAL_DIR}/${VirtualConstants.VIRTUAL_DATA_DIR}/${app.instanceId}"

// ❌ AppModule.kt:228（空壳）
val virtualRoot = context.getDir("virtual", Context.MODE_PRIVATE)     // app_virtual
```

`Context.getDir(name, mode)` 返回的是 `getFilesDir().getParent()/app_<name>`，即 **`app_virtual/`**——
和 `files/virtual/` **不是一个目录**。

### 0.3 设备实测

```
$ run-as com.nextvm.app ls -1 files/virtual/data/com.sandbox.probe_1795353657138
app_data | cache | code_cache | databases | external_cache | external_files |
files | lib | no_backup | obb | sdcard | shared_prefs          ← 12 项，真实的

$ run-as com.nextvm.app ls -R app_virtual/data/com.sandbox.probe_1795353657138
sdcard                                                          ← 1 项，空的
```

### 0.4 影响

| 后果 | 说明 |
| --- | --- |
| **`SandboxManager` 的所有路径方法都指向空壳** | `createAppDataDirectory` / `getGuestFilesDir` 等若被调用，返回 `app_virtual/...` |
| **两套目录并存** | 浪费空间；`app_virtual/data` 里甚至多出一个更早的实例 `com.sandbox.probe_1795353641715` |
| **当前功能未受明显影响** | 因为关键路径走的是 `VirtualEngine` 直接拼的 `files/virtual`，绕过了 `SandboxManager` |

**这本身是一个需要修的 bug**，但它**不阻塞**本设计——`COLLECT_OUT` 直接按真实根 `files/virtual/data/` 遍历即可。

### 0.4.1 来源追溯：**上游原项目的 bug，不是二次开发引入的**

```
$ git blame -L 224,231 -- app/src/main/java/com/nextvm/app/AppModule.kt
^63346e6 (user.email 2026-11-18 228)  val virtualRoot = context.getDir("virtual", Context.MODE_PRIVATE)

$ git blame -L 163,163 -- core/virtualization/.../VirtualEngine.kt
^63346e6 (user.email 2026-11-18 163)  virtualRoot = File(context.filesDir, VirtualConstants.VIRTUAL_DIR)
```

| 证据 | 说明 |
| --- | --- |
| `^63346e6` 前缀 | blame 的**边界提交**，该行在仓库**第一个 commit**（fork 点）就存在 |
| fork 点作者 | `334439190+woand@users.noreply.github.com`（上游作者），2026-11-18 |
| 我们的 8 个提交 | **没有一个碰过 `AppModule.kt` 或 `SandboxManager.kt`** |
| `AppModule.kt` 的 `git log --follow` | 只有 `63346e6` 一条 → 从上游以来从未被修改 |

**所以**：`VirtualEngine` 用 `filesDir`、`AppModule` 用 `getDir`，
**两个写法都是上游在同一个 commit 里写下的**，上游自己就没对齐。

### 0.4.2 影响面可能比"只有一个空壳"更大

`SandboxManager` 并不边缘，**`VirtualContext` 重度依赖它**：

```
VirtualContext.kt:36   appDir = sandboxManager.getAppDir(instanceId)
VirtualContext.kt:127  getFilesDir      -> sandboxManager.getFilesDir(instanceId)
VirtualContext.kt:135  getCacheDir      -> sandboxManager.getCacheDir(instanceId)
VirtualContext.kt:143  getCodeCacheDir  -> sandboxManager.getCodeCacheDir(instanceId)
VirtualContext.kt:153  getDatabasesDir  -> sandboxManager.getDatabasesDir(instanceId)
VirtualContext.kt:169  getExternalFilesDir -> sandboxManager.getExternalAppFilesDir(...)
VirtualContext.kt:243  nativeLibraryDir = sandboxManager.getLibDir(instanceId)
VirtualContext.kt:247  guestApkPath     = sandboxManager.getApkPath(instanceId)
```

`SandboxManager` 暴露 **25 个 public 方法**，全部以构造注入的 `virtualRoot` 为根 → **全部指向 `app_virtual/`**。

**但实测 `app_virtual/data/<instance>/` 只有空的 `sdcard`，`files/virtual/data/<instance>/` 有完整的 12 项。**

**这个矛盾目前没有解释清楚**，两种可能：

| 可能 | 含义 | 验证方式 |
| --- | --- | --- |
| **A. `PackageManagerProxy` 的 `dataDir` 覆盖了 `VirtualContext` 的** | guest 实际用 `files/virtual`，`SandboxManager` 方法被绕过 → bug 只影响没人走的路径 | 容器内跑探针看 `J01` |
| **B. `VirtualContext` 某些方法确实返回 `app_virtual`** | 那些 API 拿到空目录 → **潜在功能缺陷** | 同上 + 测能否写入 |

**关键：我们容器里还没有 `J01` 数据**（21:20 装的 v1.6 只显示 UiTestActivity，没跑 ProbeRunner）。

### 0.4.3 结论：修之前必须先实测

**不要盲改 `AppModule`**。正确顺序：

1. 装探针 v1.7 到容器 → 跑一次 → 看 `J01 getDataDir` / `J16 nativeLibraryDir`
2. `J01` 是 `files/virtual/data/<instance>` → **方案 A**（bug 无实际影响，记录即可）
3. `J01` 是 `app_virtual/data/<instance>` → **方案 B**（严重）
4. 再决定是"只让 `COLLECT_OUT` 用真实根"还是"同时修 `SandboxManager`"

### 0.5 本设计采用的路径

```
真实沙箱根      /data/user/0/com.nextvm.app/files/virtual/
guest 数据      <沙箱根>/data/<instanceId>/files/probe-report.json
专用输出目录    /data/user/0/com.nextvm.app/files/probe-out/<instanceId>/
```

**注意**：`probe-out/` 放在 `files/` 下（**与 `files/virtual/` 平级**），
不是 `files/virtual/` 内部，所以不参与 guest 的路径翻译，guest 看不到。

---

## 一、问题回顾

探针的输出目前有三条通道，**在容器内只有 logcat 可用**：

| 通道 | 容器内落点 | adb 能否取到 |
| --- | --- | --- |
| logcat 分块（主） | 不落盘 | ✅ 能（`extract-report.py` 还原） |
| `getFilesDir()/probe-report.json` | guest 沙箱内 | ⚠️ 需 `run-as`（vivo 上探针不是容器包名，`run-as com.sandbox.probe` 不存在） |
| `getExternalFilesDir()/probe-report.json` | **沙箱镜像**（被 `/sdcard` 重定向接管） | ❌ 拉不到 |

**关键难点**：探针跑在 guest 里，它的 `getFilesDir()` 是 `.../virtual/data/<instance>/files`，
而 `run-as` 只能切到**容器包名** `com.nextvm.app`——能进，但要先知道 instance 目录名。

---

## 二、设计

### 2.1 第 1 条：专用容器内输出目录

```
/data/user/0/com.nextvm.app/files/probe-out/
├── <instanceId>/                     ← 按 guest 实例分目录
│   ├── probe-report.json
│   ├── probe-early.json
│   └── meta.txt                      ← 收集时间、源路径、大小、sha256
└── index.json                        ← 全部实例的清单（供 LIST_OUT 用）
```

**与现有沙箱的关系**：
- 沙箱（guest 数据）在 `.../app_virtual/data/<instanceId>/`
- **`probe-out/` 是容器的独立目录，不是沙箱的一部分**，不参与 guest 的路径翻译
- 所以 guest **看不到**它（好事：不增加暴露面）

**为什么按 instanceId 分目录**：同一包可以装多个实例（`com.sandbox.probe_1795351786383`），必须分开，否则互相覆盖。

### 2.2 第 2 条：CmdReceiver 导出命令

在现有 5 条命令（`INSTALL`/`LAUNCH`/`LAUNCH_ACTIVITY`/`UNINSTALL`/`LIST`）基础上加 3 条：

| 命令 | 参数 | 行为 |
| --- | --- | --- |
| **`COLLECT_OUT`** | 无（或 `--es pkg` 限定） | 遍历所有 guest 沙箱，把 `files/probe-*.json` 复制到 `probe-out/<instanceId>/`，并写 `meta.txt` + `index.json` |
| **`LIST_OUT`** | 无 | 列 `probe-out/` 下的文件：`instanceId / 文件名 / 大小 / mtime / sha256 前 8 位` |
| **`CAT_OUT`** | **`--es instance <instanceId>`**<br>**`--es name <文件名>`** | 把 `<probe-out>/<instance>/<name>` 的内容**分块 base64** 打到 logcat（tag=`CmdReceiver`，格式 `OUTCHUNK v1 part i/n <b64>`） |

### 为什么 `CAT_OUT` 用两段参数（`instance` + `name`）而不是一段路径

**一段路径（`--es name <instance>/<file>`）容易出错的地方**：

| 风险 | 说明 |
| --- | --- |
| 需要解析 `/` | instanceId 本身含下划线（`com.sandbox.probe_1795351786383`），文件名含 `.`，拼起来后靠 `lastIndexOf('/')` 切分，写错就错位 |
| 路径校验复杂 | 必须同时对「instance 段」和「file 段」做 `..` 检查，容易漏 |
| shell 转义 | 斜杠在某些 shell 里需要引号，`adb shell` 的引号规则又会吃掉一层 |

**两段参数**：

```bash
--es instance com.sandbox.probe_1795351786383 --es name probe-report.json
```

| 优势 | 说明 |
| --- | --- |
| 无解析歧义 | 两段各自独立，不需要拆分字符串 |
| 校验简单 | `instance` 只允许 `[A-Za-z0-9._-]+` 且不含 `/`；`name` 只允许白名单文件名（`probe-report.json` / `probe-early.json` / `meta.txt`） |
| 不可能路径穿越 | 两段都禁止 `/` 和 `..`，天然杜绝 `../../` 逃逸 |
| 与 `LIST_OUT` 的输出对齐 | `LIST_OUT` 每行输出 `instance` 和 `name` 两列，直接喂给 `CAT_OUT` |

**为什么 `CAT_OUT` 要分块 base64**：

| 导出方式 | 可行性 | 说明 |
| --- | --- | --- |
| `run-as com.nextvm.app cat files/probe-out/x.json` | ✅ 可用 | 容器是 debuggable；但**依赖 run-as 可用**（部分 ROM 限制） |
| **logcat 分块 base64** | ✅ **最稳** | 不依赖 run-as；已有成熟提取脚本（`extract-report.py` 同样逻辑） |
| `adb pull /data/user/0/...` | ❌ | shell 无权限 |

**两种都保留**：`LIST_OUT`/`CAT_OUT` 给脚本用；`run-as` 作为人工排查的快捷方式。

---

## 三、命令用法

```bash
# 1) 让容器从各 guest 沙箱收集探针输出
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.COLLECT_OUT
adb logcat -d -s CmdReceiver | Select-String "COLLECT"

# 2) 看收集到了什么
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LIST_OUT
adb logcat -d -s CmdReceiver
#   OUT com.sandbox.probe_1795351786383/probe-report.json  12722 B  sha=3f2a1b9c

# 3) 取指定文件（分块 base64 打到 logcat）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver \
    -a com.nextvm.cmd.CAT_OUT \
    --es instance com.sandbox.probe_1795351786383 --es name probe-report.json

# 4) 或者直接 run-as（更快）
adb shell "run-as com.nextvm.app cat files/probe-out/com.sandbox.probe_1795351786383/probe-report.json" > report.json
```

**`CAT_OUT` 的 logcat 格式**（与探针的 `REPORT v1 part` 同构，便于复用提取脚本）：

```
CmdReceiver: OUTCHUNK v1 part 1/10 <base64>
...
CmdReceiver: OUTCHUNK v1 end name=<name> parts=10 bytes=12722
```

---

## 四、实现要点

### 4.1 `COLLECT_OUT` 的实现位置

放在 **`SandboxManager`**（它已经知道 `dataRoot` 和 instance 结构）：

```kotlin
/** 把各 guest 沙箱里的探针输出收集到容器独立目录。 */
fun collectProbeOutput(): List<CollectedFile> {
    val outRoot = File(context.filesDir, "probe-out")   // 详见 4.2
    val results = mutableListOf<CollectedFile>()
    dataRoot.listFiles()?.forEach { inst ->
        val files = File(inst, "files")
        if (!files.isDirectory) return@forEach
        listOf("probe-report.json", "probe-early.json").forEach { name ->
            val src = File(files, name)
            if (!src.isFile) return@forEach
            val dstDir = File(outRoot, inst.name).apply { mkdirs() }
            val dst = File(dstDir, name)
            src.copyTo(dst, overwrite = true)
            writeMeta(dstDir, src, dst)
            results += CollectedFile(inst.name, name, dst.length())
        }
    }
    writeIndex(outRoot, results)
    return results
}
```

### 4.2 `probe-out` 为什么放 `context.filesDir` 而不是沙箱

- `context.filesDir` = `/data/user/0/com.nextvm.app/files/`
- 这是**容器的私有目录**，不在 `app_virtual/` 沙箱里
- → guest 的路径翻译不会碰它，**也不增加 guest 可见面**
- → 只在容器内可读（`run-as` / logcat 导出）

### 4.3 安全边界

| 约束 | 说明 |
| --- | --- |
| **只读 guest 沙箱的 `files/`** | 不去枚举 `databases`/`shared_prefs`，避免误收集敏感数据 |
| **只复制白名单文件名** | `probe-report.json`、`probe-early.json`，不做通配 |
| **`CAT_OUT` 的 `name` 必须做路径校验** | 拒绝 `..`、绝对路径、符号链接逃逸（同 `HostReadPolicy` 的 4 条约束） |
| **`probe-out/` 不参与 guest 路径翻译** | 放在 `app_virtual/` 之外 |

### 4.4 与"宿主代读"的关系

**完全独立**：
- 宿主代读 = WebView renderer 的**读**
- `COLLECT_OUT` = 容器主进程**从 guest 沙箱读 + 写容器私有目录**
- 两者不共享代码，也不共享权限判断

---

## 五、收益

| 收益 | 说明 |
| --- | --- |
| **不再依赖 run-as 猜目录** | 容器自己知道所有 instanceId，`LIST_OUT` 一次列全 |
| **不再依赖 `/sdcard` 副通道** | 那个通道在容器内必然落到沙箱镜像，本来就无效 |
| **导出可脚本化** | `COLLECT_OUT` → `LIST_OUT` → `CAT_OUT` 三步，全部走 logcat，不依赖设备权限 |
| **给未来的 SelfCheck 用** | `SelfCheckRunner` 的结果也可以写进 `probe-out/`，同一套导出 |

---

## 六、工作量

| 项 | 工作量 |
| --- | --- |
| `SandboxManager.collectProbeOutput()`（含 meta/index） | 2 小时 |
| `CmdReceiver` 加 3 条命令（含 `CAT_OUT` 分块） | 2 小时 |
| `CAT_OUT` 的 name 路径校验（复用 HostReadPolicy 逻辑） | 1 小时 |
| 回归：装探针 → COLLECT → LIST → CAT → 校验 JSON | 1 小时 |
| **合计** | **约 0.5 天** |

---

## 七、已确认的决策

| 项 | 决策 | 理由 |
| --- | --- | --- |
| probe-out/ 目录结构 | **按 instanceId 分目录**：probe-out/<instanceId>/ | 将来要支持同一应用多实例，按包名平铺会互相覆盖 |
| CAT_OUT 参数形态 | **两段参数**：--es instance X --es name Y | 一段路径需要解析 /、校验复杂、shell 转义易出错；两段无解析歧义，且天然禁止路径穿越 |
| 沙箱真实根 | **/data/user/0/com.nextvm.app/files/virtual/** | VirtualEngine 与 PackageManagerProxy 实际用的都是它；pp_virtual/ 是 SandboxManager 用错 getDir 产生的空壳 |

## 八、执行计划

### 阶段 0：**先实测 J01，不盲改**（bug 来自上游，修它要单独评估）

**不改代码**，只做一次测量：

`ash
# 装 v1.7 探针并启动，看 J01 到底是哪个根
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL \
    --es path /sdcard/Download/probe-3be36b2.apk
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg com.sandbox.probe
adb logcat -d -s PROBE | Select-String "J01"
`

| J01 结果 | 处置 |
| --- | --- |
| iles/virtual/data/<instance> | 方案 A：bug 无实际影响，**只记录不修**，COLLECT_OUT 直接用真实根 |
| pp_virtual/data/<instance> | 方案 B：严重缺陷 → 修 provideSandboxManager，并单独回归 guest 文件读写 |

**为什么不直接修**：
- 这是上游 bug，修它会影响 SandboxManager 全部 25 个方法的行为
- pp_virtual/ 里已有 guest 的实际数据在跑（Via/探针都能启动），盲改有回归风险
- 实测只要 5 分钟，能避免无谓的改动

**工作量**：0.5 小时（含装包与采集）

### 阶段 1：SandboxManager 加收集方法

| 项 | 内容 |
| --- | --- |
| 新方法 | collectProbeOutput(): List<CollectedFile> |
| 遍历 | <真实沙箱根>/data/<instanceId>/files/ 下的白名单文件名 |
| 写出 | <filesDir>/probe-out/<instanceId>/ + meta.txt + index.json |
| 工作量 | 2 小时 |

### 阶段 2：CmdReceiver 加 3 条命令

| 命令 | 工作量 |
| --- | --- |
| COLLECT_OUT | 0.5 小时（调用阶段 1 的方法并打日志） |
| LIST_OUT | 0.5 小时 |
| CAT_OUT（两段参数 + 分块 base64） | 1.5 小时 |
| 参数校验（instance/name 白名单） | 0.5 小时 |

### 阶段 3：探针侧加环境变量开关（可选）

探针 v1.7 目前写 getFilesDir()/probe-report.json。容器会去收集它，**探针不需要改**。

但如果想减少"探针写 → 容器再复制"的一次拷贝，可以让探针直接写到 probe-out/——**不推荐**：
- 探针拿不到容器的 ilesDir（它看到的是 guest 沙箱）
- 会破坏"探针在真机和容器里行为一致"的前提

### 阶段 4：回归

`powershell
# 1) 装探针到容器
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL     --es path /sdcard/Download/probe-3be36b2.apk
# 2) 启动（探针会跑 ProbeRunner）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg com.sandbox.probe
# 3) 收集
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.COLLECT_OUT
# 4) 列表
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LIST_OUT
# 5) 取数据
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.CAT_OUT     --es instance <id> --es name probe-report.json
# 6) 校验：logcat 里的 OUTCHUNK 能还原成和 run-as 取到的一致
`

### 工期

| 阶段 | 工作量 |
| --- | --- |
| 0（修路径 bug） | 1 小时 + 回归 1 小时 |
| 1（收集方法） | 2 小时 |
| 2（CmdReceiver 3 命令） | 3 小时 |
| 4（回归） | 1 小时 |
| **合计** | **约 1 天** |
