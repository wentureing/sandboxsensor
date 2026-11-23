# P0：DiagnosticsSink + probe-out 导出

- 日期：2026-11-23
- 依据：`docs/计划-提升路线图.md` 的 P0-1 / P0-2
- 基线 tag：容器 `pre-p0-baseline` = `a528e09`

---

## 目标

1. **P0-1**：给容器加一个"测试挂钩"（`DiagnosticsSink` 接口 + NoOp），release 零开销。
2. **P0-2**：让容器把各 guest 沙箱里的探针输出收集到自己的私有目录，并能通过 `CmdReceiver` 导出。

---

## 影响范围

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `core/virtualization/src/main/java/com/nextvm/core/virtualization/diag/DiagnosticsSink.kt` | **新增** | 接口 + NoOp + 全局访问点，约 65 行 |
| `core/sandbox/src/main/java/com/nextvm/core/sandbox/SandboxManager.kt` | 修改 | 加 `collectProbeOutput()` + `writeMeta()` + `writeIndex()`，约 90 行 |
| `app/src/main/java/com/nextvm/app/CmdReceiver.kt` | 修改 | 加 3 个 action + `catOut()` + 参数校验，约 90 行 |

**合计**：1 新增 + 2 修改，约 245 行。**不触碰** hook / 路径重定向 / 权限判断。

---

## 关键设计决策

### 1. `collectProbeOutput` 用真实沙箱根，不用 `virtualRoot`

```kotlin
// ⚠️ virtualRoot = app_virtual（SandboxManager 的注入根，实测是空壳）
// 真实 guest 数据在 files/virtual/data（见 docs/records/记录-双轨沙箱路径诊断.md）
val realDataRoot = File(context.filesDir, "${VirtualConstants.VIRTUAL_DIR}/${VirtualConstants.VIRTUAL_DATA_DIR}")
```

### 2. 导出目录与沙箱平级，guest 看不到

```
/data/user/0/com.nextvm.app/files/virtual/     ← 沙箱根（guest 数据）
/data/user/0/com.nextvm.app/files/probe-out/   ← 导出目录（与 virtual 平级）
```

### 3. `CAT_OUT` 用两段参数

```bash
--es instance <instanceId> --es name <文件名>
```

理由：一段路径要解析 `/`、校验复杂、shell 转义易错；两段各自独立校验，天然禁止 `..` 穿越。

### 4. `CAT_OUT` 分块 base64 走 logcat

与探针的 `REPORT v1 part` 同构，`extract-report.py` 的分块逻辑可直接复用。
按 **3KB 原始 / 4KB base64** 切块，留出 logcat 单行上限余量。

### 5. `DiagnosticsSink` 的零开销

```kotlin
inline fun event(phase: String, detail: () -> String) {
    val s = sink
    if (s !== NoOp) s.onEvent(phase, detail())   // release 下 lambda 不执行
}
```

---

## 风险

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 遍历沙箱时碰到不存在的目录 | 低 | 全部 `isDirectory` / `exists` 判空 |
| `CAT_OUT` 路径穿越 | **中** | instance 用正则 `[A-Za-z0-9._-]{1,128}` 且禁止 `..`；name 走白名单 |
| 新增 action 影响现有 5 条命令 | 低 | `when` 只加分支，不改现有逻辑 |
| `SandboxManager` 加方法影响 DI | 低 | 只加方法，不改构造函数 |

---

## 回退方案

```powershell
git -C _ref/ShitJumpingVM revert <本次提交 sha>
# 或回到基线
git -C _ref/ShitJumpingVM reset --hard pre-p0-baseline
```

**新增文件删除即完全回退**（`DiagnosticsSink.kt` 没有调用方依赖，`collectProbeOutput` 只被新命令调用）。

---

## 验证方法

```powershell
# 1) 编译通过（CI）
# 2) 装探针并跑一次
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path /sdcard/Download/probe-3be36b2.apk
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg com.sandbox.probe

# 3) 收集
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.COLLECT_OUT
adb logcat -d -s CmdReceiver | Select-String "COLLECT"

# 4) 列表
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LIST_OUT
adb logcat -d -s CmdReceiver | Select-String "^.*OUT "

# 5) 取数据
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.CAT_OUT `
    --es instance <id> --es name probe-report.json
```

**判据**：
- `COLLECT` 日志显示复制了文件、bytes 与源一致
- `LIST_OUT` 输出 `<instance> <name> <bytes> sha=<8位>`
- `CAT_OUT` 的 `OUTCHUNK` 能还原成合法 JSON，且与
  `run-as com.nextvm.app cat files/probe-out/<id>/probe-report.json` 内容一致
- **release 构建**：`Diagnostics.isInstalled == false`

---

## 实际变更

（编码后补，附 `.actual.diff`）
