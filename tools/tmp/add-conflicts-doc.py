import io

P = "docs/架构/架构-路径转换分层现状与重复设计.md"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    s = f.read()
nl = "\r\n" if "\r\n" in s else "\n"

if "九、四类冲突的来源" in s:
    print("已存在")
    raise SystemExit(0)

ADD = """
---

## 九、四类冲突的来源（引入时间已用 git 实证）

> 这一节回答「这些重复是怎么长出来的」。
> **除冲突 1 已消除外，其余三类暂不修复**（记录在此，供表驱动重构时逐条处理）。

### 9.0 先明确：4 处独立 + 1 处同步副本

| # | 位置 | 内容 | 引入 commit | 性质 |
| --- | --- | --- | --- | --- |
| 1 | `VirtualConstants.synthetic*` | 7 个假路径函数 | `63346e6`（上游 main 头） | **上游** |
| 2 | `VirtualContext` 的 13 处 `pathFidelity` 分支 | 哪个 API 用哪个 | `63346e6`（上游） | **上游** |
| 3 | `PackageManagerProxy` 的 `pathFidelity` 分支 | `ApplicationInfo` 字段 | `63346e6`（上游） | **上游** |
| 4 | `setupAppRedirections`（18 条规则） | 宿主前缀 → 沙箱目标 | `63346e6`（上游） | **上游** |
| 5 | `native-hook.cpp` 的 `g_path_redirects` | 规则副本 | — | **由 4 同步，非独立** |

**这个 4 处结构上游就已经存在**，不是本轮引入的。

**本轮在这套结构上做了 7 次追加**：

| commit | 动作 |
| --- | --- |
| `3f9f079` | 加 `syntheticExternalDir` / `syntheticExternalCacheDir` / `syntheticObbDir` |
| `54f3649` | 加 `syntheticMediaDir` |
| `16b4f58` / `2fcfbc7` | 改 `setupAppRedirections`，引入 `guestExternalData` |
| `c0996e9` | 改宿主兜底，引入 `guestExternalDir` |
| `c15adbb` | 改 `PATH_FIDELITY_WHITELIST` |

**净效果：零收敛，并在第 4 处内部制造了重复（冲突 1）。**

---

### 冲突 1：同一目标值被定义两次 —— **已消除**

**来源**：本轮两次独立修补撞在一起。

```kotlin
// c0996e9 引入（宿主包名兜底段）
val guestExternalDir = "$sandboxDataDir/sdcard/Android/data/$guestPackageName/"

// 16b4f58 引入（App-specific external storage 段）
val guestExternalData = "$sandboxDataDir/sdcard/Android/data/$guestPackageName/"
```

**两个变量、相同值、同在 `setupAppRedirections` 内、相隔 49 行。**

**危害**：改一处不改另一处 → 宿主包名路径与 guest 自身包名路径指向不同沙箱目录 →
就是"写到一个地方、从另一个地方读"。

**已修复**：删除 `guestExternalDir` 定义，4 处引用统一为 `guestExternalData`。

---

### 冲突 2：APK/LIB 的规则不在 `setupAppRedirections` 里 —— **暂不修复**

| 路径族 | 合成路径定义在哪 | 规则在哪 |
| --- | --- | --- |
| data / external / obb / media | `VirtualConstants.synthetic*` | **`setupAppRedirections`** |
| **APK** | `syntheticApkPath` | **`setupPathFidelityRedirections`（单独两行）** |
| **LIB** | `syntheticLibDir` | **`setupPathFidelityRedirections`（单独两行）** |

**来源**：上游 `63346e6` 就是这样分的 —— `setupPathFidelityRedirections` 的类型是
`setupAppRedirections(...)` + APK + lib，所以 APK/lib 天然落在后者的补充里。

**危害（合并时）**：如果表驱动只遍历 `setupAppRedirections` 现有的 18 条，会**漏掉 APK 和 LIB**。
T39 会红，但那是事后。

**处理要求**：表里必须有 `APK` / `LIB` 两个 `PathFamily`，且它们归属
`setupPathFidelityRedirections` 而非 `setupAppRedirections`。

---

### 冲突 3：合成路径形态与规则前缀形态耦合 —— **暂不修复**

| 路径族 | A 合成路径 | D 规则前缀 | 是否天然匹配 |
| --- | --- | --- | --- |
| EXTERNAL_FILES | `/storage/emulated/0/Android/data/<pkg>/files` | 带尾斜杠 `/.../data/<pkg>/` | ✅ 前缀匹配 |
| **OBB** | `/storage/emulated/0/Android/obb/<pkg>`（**无尾斜杠**） | 原只有带尾斜杠版本 | ❌ **不匹配** → `2fcfbc7` 补了无尾斜杠变体 |
| EXTERNAL_CACHE | `/storage/emulated/0/Android/data/<pkg>/cache` | 带斜杠前缀 | ✅ |
| APK | `/data/app/~~<token>==/<pkg>-<token>==/base.apk` | 完整路径（精确） | ✅ |

**来源**：`getObbDir()` 返回**无尾斜杠**，而规则的 `from` 写的是**带尾斜杠** →
`startsWith` 不成立 → 原样返回 → 与沙箱分叉。`2fcfbc7` 为此补了 4 条无尾斜杠变体。

**危害（合并时）**：表里**每个路径族必须存"两种宿主形态"**（含/不含尾斜杠），
否则重演。而且不能靠人工判断"这个族需要哪种形态" —— 应该统一都写两种。

---

### 冲突 4：`hiddenPaths` 与 `pathRedirections` 语义相反 —— **暂不修复**

| 机制 | 匹配方式 | 结果 | native 侧有吗 |
| --- | --- | --- | --- |
| `hiddenPaths` | **精确相等** | `/dev/null` | **没有** |
| `pathRedirections` | **前缀匹配** | 翻译 | 有（`g_path_redirects`） |

**来源**：两个机制在不同时期为不同目的加上，从未统一。

**危害（合并时）**：如果把两者并成一张表，**native 侧无法表达"精确匹配"** ——
`g_path_redirects` 只有前缀语义。所以 `hiddenPaths` **不能**并入同一张表，
要么保持独立，要么给 native 侧新增一个精确匹配集合。

---

### 9.5 合并时的四个硬要求

| # | 要求 | 依据 |
| --- | --- | --- |
| 1 | 消除 `guestExternalData` / `guestExternalDir` 重复 | **已完成** |
| 2 | APK / LIB 必须作为独立 `PathFamily` 进表，且归属 `setupPathFidelityRedirections` | 冲突 2 |
| 3 | 每个路径族必须存"含/不含尾斜杠"两种宿主形态 | 冲突 3（OBB 的教训） |
| 4 | `hiddenPaths` 不并入同一张表 | 冲突 4（语义相反，native 无对应） |

### 9.6 合并的硬验证手段

`NativeHookBridge.getPathRedirectSnapshot()` 返回规则表 `Map<String, String>`。

**表驱动改造前后各导一次，diff 必须为空。** 这是唯一能证明"规则未变"的手段（规则 10）。
diff 不为空 → 说明重构引入了行为变化，必须解释，解释不通就回退。
"""

s = s.rstrip() + "\n" + ADD
with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(s)
print("已写入第九节：四类冲突的来源")
