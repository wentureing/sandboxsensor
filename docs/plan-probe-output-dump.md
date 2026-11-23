# 设计：探针输出落盘 + CmdReceiver 导出

- 日期：2026-11-23
- 来源：用户明确的两条设计
- 状态：**设计文档，待编码**

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
| **`CAT_OUT`** | `--es name <相对路径>` | 把指定文件的内容**分块 base64** 打到 logcat（tag=`CmdReceiver`，格式 `OUTCHUNK v1 part i/n <b64>`） |

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
    -a com.nextvm.cmd.CAT_OUT --es name com.sandbox.probe_1795351786383/probe-report.json

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

## 七、待你确认的两点

1. **`probe-out/` 是否按 instanceId 分目录**？
   —— 我按"分目录"设计（同一包多实例不覆盖）。若你希望"最新一份覆盖旧份"，可以改成 `probe-out/<pkg>/latest.json`。

2. **`CAT_OUT` 是否需要支持 `--es instance` 过滤**？
   —— 目前设计用 `name`（含 instanceId 的相对路径）唯一定位，够用；如果你的脚本更习惯按 instance + 文件名两段参数，可以改。
