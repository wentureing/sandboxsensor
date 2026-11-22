# T2 记录：探针在 ShitJumpingVM（+ 补丁 p1）容器内的完整数据

- 日期：2026-11-22 16:39–16:42（设备时间）
- 设备：vivo V2463A / Android 15 / API 35 / arm64-v8a
- 容器：`com.nextvm.app`（含补丁 p1：`ActivityInfo` 资源字段归零 + `CmdReceiver`）
- 探针：`com.sandbox.probe` v1.1（Provider 入口）
- 原始数据：`../probe-reports/t2-163831.txt`、`report-t2-163831-1/2.json`、`early-t2-163831-1.json`

---

## 1. 结论速览

| 项 | 结果 |
| --- | --- |
| 命令行安装（`CmdReceiver` INSTALL） | ✅ `Success(data=VirtualApp(packageName=com.sandbox.probe...))` |
| 命令行启动（LAUNCH） | ✅ `Success(data=kotlin.Unit)` |
| 探针 4 个 PHASE | ✅ 全部出现 |
| `EARLY` 早期快照 | ✅ 1 块，Provider 阶段落盘成功 |
| **`REPORT` 完整报告** | ✅ **8 块 + 9 块，10,350 / 11,697 bytes** |
| `Resources$NotFoundException` | **✅ 0 次（p1 止血生效）** |
| `am_crash` | **✅ 0 次** |
| 容器重启后再跑（T2b） | ❌ 主进程 `RenderThread` native abort（GPU 驱动） |

**一句话：p1 补丁完全生效，方案 A（真路径）的 T2 完整数据已拿到。**

---

## 2. 一个必须记住的坑：装 APK 的路径

容器自己的 native hook 把 `/sdcard`、`/storage/emulated/0` 等前缀重定向到沙箱
（`NextVM-Native: Path redirect added: /sdcard/ -> .../virtual/data/<instance>/sdcard`），
**导致它自己也读不到 push 到 `/sdcard/...` 的 APK**：

```
INSTALL /sdcard/Android/data/com.nextvm.app/files/inbox/probe.apk
  -> Error(Failed to parse APK: APK file not found: ...)
```

正确做法（已验证）：

```powershell
# 1) 推到 /data/local/tmp（shell 可写，不在重定向范围）
adb push probe.apk /data/local/tmp/probe.apk

# 2) 设备端管道复制进 app 私有目录（宿主机 stdin 重定向不可靠，只能写 200 字节）
adb shell "cat /data/local/tmp/probe.apk | run-as com.nextvm.app sh -c 'cat > files/inbox/probe.apk'"

# 3) 用私有路径安装
adb shell am broadcast -n com.nextvm.app/.CmdReceiver \
  -a com.nextvm.cmd.INSTALL --es path /data/user/0/com.nextvm.app/files/inbox/probe.apk
```

---

## 3. T1（直接安装） vs T2（容器内）核心差异

mode 自动判定：`installed` → **`container:dir-other(...)`**（探针自检逻辑有效）

| 项 | T1 | T2 | 判定 |
| --- | --- | --- | --- |
| `J01 getDataDir` | `/data/user/0/com.sandbox.probe` | `<host>/files/virtual/data/com.sandbox.probe_<id>` | **方案 A 真路径** |
| `J15 sourceDir` | `/data/app/.../base.apk` | `<host>/files/virtual/apks/..._<id>.apk` | 沙箱 APK |
| `J16 nativeLibraryDir` | `/data/app/.../lib/arm64` | `<sandbox>/lib` | 沙箱 lib |
| `J08/J10/J11/J12` 外部存储 | `/storage/emulated/0/...` | `<sandbox>/sdcard/...` | **外部存储已重定向** |
| **`J26 /data/data/<pkg>/files`** | ok | **FAIL(ENOENT)** | ★ 字面量路径失效（方案 A 的必然代价） |
| `J18 deviceProtectedDataDir` | `/data/user_de/0/com.sandbox.probe` | `/data/user_de/0/com.nextvm.app` | **未虚拟化，泄漏宿主** |
| `J19 ApplicationInfo.uid` | `10294` | **`0`** | 容器缺陷：uid 未设置 |
| `J20 targetSdk` | `35` | **`0`** | 容器缺陷：targetSdk 未设置 |
| `J21 getInstalledPackages` | `356` | **`1`** | 包可见性限制（只能看到自己） |
| `J22 myUid == officialUid` | true（10294） | true（10300） | 未伪造 UID，检测 #1 仍可通过 |

差异项合计 **48 项**。

---

## 4. 容器缺陷清单（更新）

| 优先级 | 缺陷 | 状态 |
| --- | --- | --- |
| P0 | `ActivityInfo` icon/logo/banner/roundIcon 未重映射 | ✅ **p1 已修复（实测 0 崩溃）** |
| P0 | `getResources()` 抛异常 | ❌ 仍未修（但 p1 后 Activity 能启动，说明暂不阻塞） |
| **P1** | `ApplicationInfo.uid=0` / `targetSdk=0` | ❌ 新发现，影响 App 自检逻辑 |
| P1 | `deviceProtectedDataDir` 未虚拟化 | ❌ 新发现，泄漏宿主包名 |
| P1 | `getInstalledPackages` 只返回 1 个 | ❌ 新发现，影响依赖包列表的 SDK |
| P2 | 主进程 `RenderThread` native abort | ❌ `debug.hwui.renderer=skiagl` **无效**（栈仍是 vulkan.adreno → libllvm-glnext） |
| P2 | 自身路径重定向导致读不到 `/sdcard` 下的 APK | ⚠️ 可用私有目录绕过 |

---

## 5. T2b（容器重启后）失败分析

```
am_crash: [2736,0,com.nextvm.app,553139782,Native crash,Aborted,unknown,0,0]
Fatal signal 6 (SIGABRT) in tid ... (RenderThread), pid ... (com.nextvm.app)
#00 libc abort+172
#01-#05 libllvm-glnext.so
#06-#09 vulkan.adreno.so
#10-#16 libhwui GrVkPipeline::Make
```

- 设置 `debug.hwui.renderer=skiagl` 后**崩溃栈完全一致** → 该属性未生效，或崩溃不在 HWUI 后端选择层
- 因为主进程崩，`CmdReceiver` 的 LAUNCH broadcast 无人处理 → 探针没跑
- 可能诱因还包括容器的 native GOT hook（`Late-patch: GOT hooks applied to 2 newly loaded libraries`）干扰了驱动库

**结论**：T2b 在 vivo 这台设备上暂时无法完成；但不影响主结论（T2a 已给出完整数据）。

---

## 6. 对决策的影响

`J26` 在 T2 下 FAIL，正是 `03`/`06` 文档预测的"方案 A 必然代价"。

→ 若要兼容硬编码 `/data/data/<pkg>` 的 SDK，必须走 **B1（虚拟标准路径 + 路径视图层）**，见 `01 §5` 决策表。
