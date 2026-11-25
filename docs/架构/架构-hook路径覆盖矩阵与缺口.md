# 架构：hook 路径覆盖矩阵与缺口（含方案A 实测结论）

- 日期：2026-11-25
- 状态：**当前生效**（基于 `baseline-v150` @ `92d4367` 实测）
- 用途：改路径重定向前必读 —— 先查这张表，确认要覆盖的 syscall 有没有 hook
- 相关：`docs/设计/设计-按进程区分的宿主路径兜底.md`、`docs/输出/变更/20261125-planA-host-external-fallback.md`

---

## 一、native 层 hook 覆盖矩阵（实测 `native-hook.cpp` L1525–1542）

| libc 函数 | 是否 hook | 是否走 `redirect_path` | 说明 |
| --- | --- | --- | --- |
| `open` | ✅ | ✅ | 写路径带 `O_CREAT` 时会传 mode |
| `openat` | ✅ | ✅ | 同上（相对 dirfd） |
| `fopen` | ✅ | ✅ | C 标准库入口 |
| `access` | ✅ | ✅ | 权限探测 |
| `stat` | ✅ | ✅ | |
| `lstat` | ✅ | ✅ | |
| `readlink` | ✅ | ✅ | |
| `opendir` | ✅ | ✅ | 目录列举 |
| **`mkdir`** | ❌ **无** | — | **缺口** |
| **`mkdirat`** | ❌ **无** | — | **缺口** |
| `rename` / `unlink` / `rmdir` | ❌ 无 | — | 未验，疑似缺口 |
| `__openat`（bionic 内部别名） | ❌ 无 | — | 部分 API 可能绕过 `openat` |

**关键结论**：

> **容器重定向的是「文件操作」，不是「目录操作」。**
> 任何 `mkdir()` 都会在**宿主真实路径**上创建目录。

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

**为什么父目录不存在**：`mkdir(".../files/offline")` **没被重定向**，
真实建在宿主 `.../data/com.nextvm.app/files/offline/`，
而 `<sandbox>/host_ext/files/offline/` 从头到尾没人建。

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
| **2** | **补 `mkdir`/`mkdirat` hook** | 让目录创建也走 `redirect_path` | 改动面大：`mkdir` 被所有库调用，路径重定向可能影响容器自身；需要 `mkdir_p` 语义（逐级建） |
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

> **路径重定向 = 前缀替换。它不创建目录、不保证可写、不处理符号链接。**
> 所以任何新规则都必须回答三个问题：
> 1. 写入方会不会先 `mkdir`？（若是 → 目录会落宿主 → 目标树不完整）
> 2. 目标前缀的目录由谁预建？
> 3. 失败时用户能否察觉？（当前是**完全静默**）
