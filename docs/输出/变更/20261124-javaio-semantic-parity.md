# 变更计划：Java IO hook 语义等价重构 + 调试工具增强 + debug 日志全开

- 日期：2026-11-24
- 用户指令（直接执行）：① 执行 hook 核心的语义等价重构（严重阻碍开发）；
  ② 需要更多更强大的调试工具；③ 默认 debug 日志输出全部。

---

## 一、目标

| # | 目标 | 判据 |
| --- | --- | --- |
| 1 | `hooked_java_linux_open/access` 与 AOSP **行为等价**（不只是成功路径） | Via 可交互（原生 UI 可点）+ W38 仍通过 |
| 2 | 调试工具增强：最近 N 次 open 的**路径 + errno**、异常抛出记录 | trace 里能直接看到失败的路径和 errno |
| 3 | debug 日志默认全开 | `DebugFlags` 快照里 `verbose=true`，无需 setprop |

## 二、影响范围

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `core/hook/src/main/cpp/native-hook.cpp` | 修改 | 重写 open/access；JNI 引用缓存；EINTR 重试；环形缓冲；新增 2 个 JNI 导出 |
| `core/hook/.../NativeHookBridge.kt` | 修改 | 新增 `nativeGetRecentOpens()` / `nativeClearRecentOpens()` 声明与包装 |
| `core/common/.../DebugFlags.kt` | 修改 | `VERBOSE` 默认 true；快照默认值同步 |
| `core/common/.../DebugTrace.kt` | 修改 | `detail` 的默认值改 true |
| `core/virtualization/.../VirtualApplicationManager.kt` | 修改 | bind 完成时把 recentOpens 落盘 |

## 三、逐文件 diff 预览

### 3.1 native：JNI 引用缓存 + 语义对齐

```cpp
// 新增（首次使用时解析并永久缓存，避免每次 open 都 FindClass）
static jclass   g_fd_class;      static jmethodID g_fd_ctor, g_fd_set_int;
static jclass   g_errno_class;   static jmethodID g_errno_ctor;

static bool java_io_refs(JNIEnv* env);          // 解析上述引用
static void throw_errno(JNIEnv* env, const char* fn, int err);   // 抛 libcore.io.ErrnoException

// open：改前 -> 失败抛 FileNotFoundException，无 EINTR 重试
// open：改后 -> TEMP_FAILURE_RETRY 语义 + 失败抛 ErrnoException("open", errno)
int fd;
do { fd = ::open(path, flags, mode); } while (fd == -1 && errno == EINTR);
if (fd == -1) { record_open(path, errno, flags); throw_errno(env, "open", errno); return nullptr; }

// access：改前 -> 失败返回 JNI_FALSE（不抛）
// access：改后 -> 失败抛 ErrnoException("access", errno)；成功返回 true
```

### 3.2 native：最近 N 次 open 的环形缓冲（调试增强）

```cpp
struct OpenRecord { char path[192]; int err; int flags; };
static const int RECENT_CAP = 32;
static OpenRecord g_recent[RECENT_CAP];
static std::atomic<int> g_recent_seq{0};
static std::mutex g_recent_mu;

static void record_open(const char* path, int err, int flags);   // 记录（含成功，err=0）
JNIEXPORT jstring ..._nativeGetRecentOpens(JNIEnv*);             // 多行：seq|err|flags|path
JNIEXPORT void    ..._nativeClearRecentOpens(JNIEnv*);
```

记录粒度：只记**失败**或**发生翻译**的调用（避免刷屏），成功且未翻译的不记。

### 3.3 Kotlin：调试开关默认全开

```kotlin
// DebugFlags.snapshot 里 VERBOSE 的默认值：false -> true
// DebugTrace.detail：isOn(VERBOSE, false) -> isOn(VERBOSE, true)
```

### 3.4 bind 完成时落盘 recentOpens

```kotlin
DebugTrace.event("javaIo", "recent opens:\n" + (nativeHookBridge.recentOpens() ?: "n/a"))
```

## 四、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | `libcore.io.ErrnoException` 在目标 API 上不可见（@hide） | 先 `FindClass` 探测；拿不到时**退回 FileNotFoundException**并记 fail（不让 hook 变成崩溃源） |
| 2 | 抛异常后没有 `ExceptionCheck` 清理，可能导致后续 JNI 调用失败 | 抛异常前不残留局部引用；返回后调用方（IoBridge）会清异常 |
| 3 | 环形缓冲加锁影响性能 | 只在"失败/翻译"时记录，正常路径不碰锁 |
| 4 | 默认 verbose 让日志量增大 | 单文件 1.5MB 截断（已有）；`recent opens` 只记异常和翻译 |

## 五、回退方案

```powershell
git -C _ref/ShitJumpingVM revert <sha>
# 或运行期直接关掉：setprop debug.nextvm.javaIo 0（默认已是 0）
```

## 六、验证方法

| 步 | 判据 |
| --- | --- |
| 1 | `setprop debug.nextvm.javaIo 1` + 重建 guest 进程（UNINSTALL/INSTALL） |
| 2 | **Via 可交互**（原生工具栏/菜单可点）—— 这是本次重构的核心验收 |
| 3 | W38 仍 OK（Java 层写 `/sdcard` 落沙箱） |
| 4 | W3 仍 `ASSET_OK` |
| 5 | trace 里能看到 `recent opens` 的失败路径与 errno |
| 6 | `debug.nextvm.javaIo 0` 时与现在行为一致（无回归） |
