# 架构：hook 路径覆盖矩阵与缺口（含方案A 实测结论）

- 日期：2026-11-25
- 状态：**本文 §一 的 hook 矩阵已被代码推翻**（快照基于 `92d4367`，之后已 hook 写操作）。
  **最新事实见 [`架构-hook实现现状-代码为准.md`](架构-hook实现现状-代码为准.md)**；
  本文保留 §二/§三 的**实测结论**（方案A 为何没解决问题）仍然有效。
- 用途：改路径重定向前必读 —— 先查覆盖矩阵，确认要覆盖的 syscall 有没有 hook
- 相关：`docs/设计/设计-按进程区分的宿主路径兜底.md`、`docs/输出/变更/20261125-planA-host-external-fallback.md`

---

## 一、native 层 hook 覆盖矩阵（**已按 `6b5cfbe` 代码更正**）

| libc 函数 | 是否 hook | 是否走 `redirect_path` | 说明 |
| --- | --- | --- | --- |
| `open` / `open64` | ✅ | ✅ | 写路径带 `O_CREAT` 时会传 mode（`:674`） |
| `openat` / `openat64` | ✅ | ✅ | 同上（相对 dirfd，`:710`） |
| `fopen` / `fopen64` | ✅ | ✅ | C 标准库入口（`:898`） |
| `access` | ✅ | ✅ | 权限探测（`:736`） |
| `stat` / `lstat` | ✅ | ✅ | |
| `readlink` | ✅ | ✅ | |
| `opendir` | ✅ | ✅ | 目录列举（`:1374`） |
| **`mkdir`** | ✅ **已 hook** | ✅ | 只建最后一级（AOSP 语义，`:1390`） |
| **`mkdirat`** | ✅ **已 hook** | ✅ | `:1406` |
| **`unlink` / `unlinkat`** | ✅ **已 hook** | ✅ | `:1418` / `:1434` |
| **`rename`** | ✅ **已 hook** | ✅ | 源与目标各自翻译（`:1446-1447`） |
| `rmdir` | ❌ 无 | — | **仍缺**（`unlink` 不能替代删目录） |
| `__openat`（bionic 内部别名） | ❌ 无 | — | 部分 API 可能绕过 `openat` |

**关键结论（2026-11-25 更正）**：

> **目录操作（`mkdir`/`mkdirat`）已被重定向；旧的「只重定向文件操作」结论作废。**
> 但**「建目录」与「打开文件」可能走不同层**（Java 层落宿主 / native 层落沙箱），
> 跨层时仍会出现"目标父目录不存在"。这才是方案A 的真实失败机制，见 §三。

---

## 二、这个缺口造成的三个可观测症状

### 2.1 宿主出现空目录树

via 启动时 `mkdir` 自己的 8 个功能目录，全部真实创建在宿主：

```
/storage/emulated/0/Android/data/com.nextvm.app/files/
  filters/ font/ gm/ logs/ offline/ proxy/ syncing/ tabs/
```

**全部为空**（`find -type f` 无结果）—— 因为只有 `mkdir` 落宿主，文件写入被重定向去别处。

**这让"看目录结构"看起来像"容器把 guest 数据写到宿主了"**，实际只是**目录壳**。

### 2.2 重定向目标目录树不完整 → 写入失败

方案A 加规则后（`.../data/com.nextvm.app/` → `<sandbox>/host_ext/`）：

```
via 调 open(".../files/offline/xxx.mht", O_CREAT)
  → redirect_path → "<sandbox>/host_ext/files/offline/xxx.mht"
  → real_open → ENOENT（host_ext/files/offline/ 不存在）
  → 返回 -1，静默失败
```

**为什么父目录不存在**（2026-11-25 更正归因）：`mkdir` 其实**已被重定向**，但当时
`debug.nextvm.javaIo=0`（Java 层 hook 未安装）、`NS_HOOK_SYSTEM_LIBS=0`（`libjavacore.so` 未被 patch），
所以 via 的 `File.mkdirs()`（走 `UnixFileSystem.createDirectory0`）仍落在**宿主**，
而 `open` 一旦走的是被 patch 的库（native 路径）就落到**沙箱** → 沙箱目标父目录无人建。
**不是"mkdir 没 hook"，是"建目录与打开文件走了不同层"。**

### 2.3 `hooked_open` 没有 ENOENT 兜底

```cpp
// native-hook.cpp L673-687
std::string redirected = redirect_path(path);
const char* actual_path = redirected.empty() ? path : redirected.c_str();
return real_open(actual_path, flags, mode);   // ← 父目录缺失直接失败
```

---

## 三、方案A 实测结论（2026-11-25）

| 判据 | 结果 |
| --- | --- |
| APK 含新代码 | ✅ `classes4.dex` 有 `host_ext` ×2、`Host external fallback` ×1 |
| `host_ext` 目录被创建 | ✅ `<instance>/host_ext` 存在（11:29） |
| 宿主 `offline/` 不再产生文件 | ✅ 仍为空 |
| **沙箱 `host_ext/` 有文件** | ❌ **空**（连子目录都没有） |
| **via 离线列表能看到条目** | ❌ 仍然看不到 |
| 数据库 `tabs`/`bookmarks` | ❌ 仍为 0（`history` 从 2 → 3） |
| 容器导入 APK | ✅ 未受影响 |

**判定：方案A 未解决问题。** 但它**证明了重定向链是通的**（`host_ext` 目录被建说明
`redirect_path` 生效），失败点精确落在「**目标父目录不存在**」。

**不是"规则没生效"，是"生效了但写不进去"。**

---

## 四、三种修法（按侵入性排序）

| # | 方案 | 做法 | 代价 |
| --- | --- | --- | --- |
| **1** | **`hooked_open` 兜底**（最小） | 写入失败且 `errno == ENOENT` 时，`mkdir -p` 重定向后路径的父目录，再重试一次 | 每个 `open` 多一次失败重试；需防递归（mkdir 也走 hook 时） |
| **2** | ~~补 `mkdir`/`mkdirat` hook~~ | ~~让目录创建也走 `redirect_path`~~ | **已完成**（`:1390`/`:1406`）；但只在"被 patch 的库"或"Java 层 hook 打开"时生效 |
| **3** | **预建目录树** | 容器在 guest 启动前，把宿主的 8 个目录结构**镜像**到沙箱 | 治不了"新目录"；via 换版本就失效 |

**推荐 1**：最小、可控、治本（覆盖所有"目标父目录缺失"场景，不只 via）。

**注意**：方案 1 里 `mkdir -p` 必须**直接调 `real_mkdir`**（不经过 `redirect_path`），
否则会与"父目录缺失"互相递归。

---

## 五、这个缺口的历史影响（回头看以前的误判）

| 曾经的观察 | 当时的解读 | 现在看 |
| --- | --- | --- |
| 宿主 `.../data/com.nextvm.app/files/tabs/` 有 2 个文件 | "via 用 getOpPackageName 逃到宿主"（部分对） | 更准确：**目录在宿主，文件被重定向到哪取决于路径**——`tabs` 用的是 `getOpPackageName` 拼路径且写入被重定向到沙箱失败，最终"文件"能落宿主的只有那些路径未被重定向的 |
| 宿主 8 个目录全为空 | "容器把数据写到宿主了" | **只是目录壳**，文件从未成功写入 |
| `tabs.file_path` 记宿主路径 | "记录错了" | **记录是对的**（via 确实这么调用的），错的是容器没让配套的文件操作也落沙箱 |

---

## 六、验证这张表的方法（新改动前必做）

```powershell
# 1) 确认 native hook 清单（源码）
Select-String -Path core/hook/src/main/cpp/native-hook.cpp `
  -Pattern '^\s*\{"(open|openat|access|stat|lstat|readlink|fopen|opendir|mkdir)' |
  ForEach-Object { $_.Line.Trim() }

# 2) 确认某个 syscall 是否真的被拦截（真机）
#    在 guest 里触发该操作，看 logcat 是否有 "open: <原> -> <新>"
adb logcat -d | Select-String "open: .* -> "

# 3) 确认重定向目标目录是否存在（最容易漏的一步）
adb exec-out run-as com.nextvm.app ls -la <instance>/<new_prefix>/
```

**第三步是本轮的教训**：之前只验证了"规则注册了"，没验证"目标目录存在"。

---

## 七、一条判据

> **路径重定向 = 前缀替换。它按 AOSP 语义只建最后一级目录（不建父目录）、不保证可写、不处理符号链接。**
> 且它作用于「被 patch 的库 + 已安装的 Java 层 hook」，**不是全进程生效**（见 `架构-hook实现现状-代码为准.md` §四/§五）。
> 所以任何新规则都必须回答三个问题：
> 1. 写入方会不会先 `mkdir`？若会 → 它走哪一层？（Java 层落宿主 / native 层落沙箱 → 目标树可能不完整）
> 2. 目标前缀的目录由谁预建？
> 3. 失败时用户能否察觉？（当前是**完全静默**）
