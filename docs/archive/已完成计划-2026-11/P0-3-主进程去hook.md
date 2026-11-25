# P0-3：主进程不装 hook + 删除路径豁免

> 原 §二点五


### 2.5.1 为什么插进来

实测（`records/记录-P28与权限传导-完整结论.md`）：

| 检查 | 结果 |
| --- | --- |
| `run-as com.nextvm.app cat /sdcard/Download/x.apk` | **Permission denied**（DAC 660，非本人） |
| `run-as com.nextvm.app cat /data/local/tmp/x.apk` | **成功** |
| `appops get com.nextvm.app MANAGE_EXTERNAL_STORAGE` | **default**（未授权） |

**结论**：p18 的 `/sdcard/Download` 豁免**从根上无效** —— 它只作用于 `redirect_path`，
而 DAC 在它之前就拒了。**继续加豁免是在错误的层做修补。**

### 2.5.2 设计：让主进程不装 hook

```kotlin
/ NativeHookBridge.initNativeHooks 开头
val procName = android.app.Application.getProcessName()
val pkg = (context ?: appContext)?.packageName
if (pkg != null && procName == pkg) {
    Timber.tag(TAG).i("initNativeHooks skipped: container main process")
    initialized = true
    return false
}
```

**依据**：
- `CmdReceiver` 在主进程（manifest 无 `android:process`）
- guest 在 `com.nextvm.app:p0`（改名为 guest 包名）
- `libc.so` 属系统库，`NS_HOOK_SYSTEM_LIBS=0` → 主进程的 `open` 本来就不被重定向
- `run-as` 子进程（不装 hook）能读 `/data/local/tmp` 就是证明

### 2.5.3 收益

| 项 | 改前 | 改后 |
| --- | --- | --- |
| 装包路径 | 依赖 `is_import_read_exempt`（6 个函数） | 主进程直接读真实路径，**不需要豁免** |
| `/sdcard/Download` | 豁免无效（DAC 拒） | 仍读不到（DAC），但**不再假装能读** |
| `/data/local/tmp` | 需要豁免 | **不需要**（644） |
| 攻击面 | guest 可读三个前缀 | **只剩 DAC 允许的** |

### 2.5.4 四阶段

| 阶段 | 内容 | 风险 |
| --- | --- | --- |
| 1 | 验证 `CmdReceiver` 只在主进程；`AntiDetectionEngine.hidePath` 等调用方是否跨进程 | 低（只读） |
| 2 | `initNativeHooks` 加进程判断，主进程 return | 中（主进程少了 hook，若有功能依赖会暴露） |
| 3 | **删除 `is_import_read_exempt` 及 6 处调用** | 中（先确认阶段 2 生效） |
| 4 | 导入通道固定 `/data/local/tmp`，文档移除 `/sdcard/Download` | 低 |

### 2.5.5 验收

```powershell
# 1) 主进程不再装 hook
adb logcat -d | Select-String "initNativeHooks skipped: container main process"
# 2) 装包走 /data/local/tmp 成功，且日志里没有 "import exempt"
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path /data/local/tmp/x.apk
adb logcat -d -s CmdReceiver | Select-String "INSTALL"
# 3) guest 仍能正常启动（hook 在 guest 进程仍生效）
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg <pkg>
```

---