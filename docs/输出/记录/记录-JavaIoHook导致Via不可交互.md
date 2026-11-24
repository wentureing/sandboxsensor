# 记录：Java IO hook 导致 Via 完全不可交互（已默认关闭）

- 日期：2026-11-24
- 结论：**`debug.nextvm.javaIo=1`（v141 的 Java 层文件 I/O hook）会让 Via 从
  「主页显示源码但仍可交互」退化为「完全不能交互」。已把默认值改为关闭。**
- 判定者：用户（在设备上做了 `javaIo=0/1` 对照）

---

## 一、现象对比

| 配置 | Via 表现 |
| --- | --- |
| **`javaIo=1`**（v141 打开） | 主页显示源码；**原生工具栏/菜单也点不动**，完全不能交互 |
| **`javaIo=0`**（等价于没有 v141） | 主页显示源码；**其余交互正常** |

> 主页源码是独立的 MIME 问题（`记录-W2-text-plain机制收敛.md`）。
> 本次只讨论「交互退化」，两者必须分开看。

## 二、怎么确认的（对照实验）

```powershell
adb shell setprop debug.nextvm.javaIo 0      # 关
# 关键：LAUNCH 不会重启已运行的 guest 进程，必须让进程重建
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.UNINSTALL --es pkg mark.via
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.INSTALL --es path /data/local/tmp/via.apk
adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH --es pkg mark.via
```

`debug-trace.log` 确认开关确实生效：

```
[Dbg/javaIo] SKIPPED by debug.nextvm.javaIo=0（等价于没有 v141）
[Dbg/javaIo] stats after bind: total=0 fail=0 translated=0 translate=0
```

### 踩过的坑：LAUNCH 不重启进程

第一次设 `javaIo=0` 后直接 `LAUNCH`，Via 的 PID 一直没变（`ETIME` 在增长），
hook 早已在该进程里装好，开关根本没被读取。**必须让 guest 进程重建**（UNINSTALL+INSTALL，
或用户手动关闭后重启），对照才成立。

## 三、根因（已定位到具体的语义偏差）

`hooked_java_linux_open` / `hooked_java_linux_access` 与 AOSP 的实现不等价：

| 项 | AOSP | 本实现 | 后果 |
| --- | --- | --- | --- |
| `Linux.open` 失败 | 抛 `ErrnoException("open", errno)` | 抛 `FileNotFoundException(path)` | **`catch (ErrnoException e)` 判断 ENOENT/EACCES 的调用者捕获不到** → 异常直接上抛。`IoBridge.open` 这条路径结果相同（它会把 ErrnoException 转成 FileNotFoundException），但直接调 `Os.open` 的 `FileChannel`、`NativeCrypto`、部分 `RandomAccessFile` 路径会走错分支 |
| `Linux.access` 失败 | 抛 `ErrnoException` | **返回 false，不抛** | `File.canRead()/canWrite()` → `UnixFileSystem.checkAccess` → `Libcore.os.access`，依赖异常分支的代码行为改变 |
| EINTR | `TEMP_FAILURE_RETRY(open(...))` | 无 | 偶发 EINTR 直接失败 |

另外 `java_io_make_fd` 每次调用都 `FindClass` + `GetMethodID`（未缓存），
失败时返回 null 且不抛异常 —— 若 `setInt$` 拿不到，上层会拿到"无效 FileDescriptor"而不是明确错误。

## 四、当前处置

| 项 | 状态 |
| --- | --- |
| v141 代码 | **保留**（不删，供修复后重新启用） |
| 默认值 | **改为关闭**（`DebugFlags.isOn(JAVA_IO, false)`），并在代码注释里写明原因与修复方向 |
| 打开方式 | `adb shell setprop debug.nextvm.javaIo 1` + 让 guest 进程重建 |
| 需要同步更新的文档 | `docs/现状/README.md` 的能力状态表（Java 层重定向改为「已实现但默认关闭」） |

## 五、将来修复的方向（若还要这个能力）

1. **异常语义对齐**：`open` 失败时抛 `libcore.io.ErrnoException`；`access` 失败时也抛。
   需要找到 `ErrnoException` 的构造签名（`(String functionName, int errno)`）并正确构造。
2. **加 `TEMP_FAILURE_RETRY`**。
3. **缓存 `FileDescriptor` 的 ctor/`setInt$` 的 jmethodID**（首次解析后静态保存）。
4. **必须的回归集**：Via 可交互（原生 UI 可点）+ W38（Java 层写 `/sdcard` 落沙箱）+ W3（asset 正常）。
5. 修复后默认值仍保持关闭，只在需要"假路径可读"的场景按 guest 打开（白名单）。

## 六、教训

| # | 教训 |
| --- | --- |
| 1 | **替换 boot classpath 的 native 方法，必须逐项对齐 AOSP 语义**（异常类型、重试、fd 构造），只对齐"正常路径"是不够的 |
| 2 | **验证覆盖要在"真实应用"上做**，不能只看探针的 W38 —— 探针只走了一条成功路径 |
| 3 | **进程复用会掩盖开关效果**：LAUNCH 不重启，改 property 后必须重建进程 |
| 4 | 能力开关默认值应取"安全侧"：新 hook 默认关闭，验证充分后再改默认 |
