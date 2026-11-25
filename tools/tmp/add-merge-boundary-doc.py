import io

P = "docs/架构/架构-路径转换分层现状与重复设计.md"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    s = f.read()
nl = "\r\n" if "\r\n" in s else "\n"

if "十、合并边界的判据" in s:
    print("已存在")
    raise SystemExit(0)

ADD = """
---

## 十、合并边界的判据（**不要为了合并而合并**）

> 用户明确要求：「不要为了合并而合并，允许使用配置表 + 各自独立的使用方式，
> 对于功能差异大的不要合并。」

### 10.1 为什么会有人想全部合并 —— 以及为什么不该

看到"同一份知识写在 4 处"，第一反应是"全并成一张表、所有消费方都查表"。
**这个反应是错的**，因为那 4 处的**共同点只有"假路径长什么样"**，
其余全是差异：

| 消费方 | 除了"知道假路径"，还要做什么 | 差异性质 |
| --- | --- | --- |
| `VirtualContext` 的 13 处 | `sandboxManager.ensureDir(...)` **建目录**（副作用） | 有 I/O |
| `PackageManagerProxy` | 设 `ApplicationInfo` 的**不同字段**，含**反射** | 无 I/O，有反射 |
| `setupAppRedirections` | `addPathRedirection(...)` **注册规则**，且**时机不同**（guest bind） | 改全局状态 |
| C++ `g_path_redirects` | **跨语言**，由 Kotlin 同步过来 | 同步副本 |

**把"建目录 / 设字段 / 注册规则"三套控制流塞进一个表，是耦合，不是精简。**

### 10.2 判据：数据合并，控制流不合并

| 判断对象 | 合并 | 判据 |
| --- | --- | --- |
| **假路径模板**（`/data/user/0/<pkg>`） | ✅ | 纯数据，无副作用 |
| **沙箱相对位置**（`files` / `sdcard/Android/data/<pkg>/files`） | ✅ | 纯数据 |
| **宿主前缀变体**（含/不含尾斜杠） | ✅ | 纯数据 |
| **谁在什么时候用** | ❌ | 控制流 |
| **要不要先 ensureDir / mkdirs** | ❌ | 副作用 |
| **设哪个字段 / 是否反射** | ❌ | 副作用 |
| **注册时机**（bind 时 / 启动时） | ❌ | 控制流 |

**一句话：表只承载「数据」，不承载「控制流」。**

### 10.3 明确不合并的四类

| 对象 | 不合并的理由 |
| --- | --- |
| `hiddenPaths` | **精确匹配**（`/system/bin/su` 只隐藏这一个），与 `pathRedirections` 的**前缀匹配**语义相反。合并后无法表达"只精确匹配这一条" |
| C++ `g_path_redirects` | **跨语言**（要 native 反调 Kotlin，收益远小于风险）；且它是 `pathRedirections` 的**同步副本**，不是独立知识 |
| A 族（`Linux.*` 抛异常）/ B 族（`UnixFileSystem.*0` 返回空值） | **Android 本身的真实差异**，不是我们的重复。合并会掩盖契约 |
| 三个 `setup*Redirections` 入口 | 注册**时机与组合方式**不同（`setupPathFidelityRedirections` 内部调 `setupAppRedirections`） |

### 10.4 允许的"半合并"

**用户明确允许**：配置表 + 各自独立的使用方式。

所以正确的形态是：

```
一张数据表（PathFamilies）
   ├── synthetic(family, pkg)          假路径模板
   ├── sandboxPath(family, instanceId, pkg)  沙箱相对位置
   └── hostForms(family, pkg)           宿主前缀变体列表

消费方各自保留自己的逻辑，只在"取值"时查表：
   VirtualContext.getFilesDir() { ensureDir(...); return File(PathFamilies.synthetic(FILES, pkg)) }
   PackageManagerProxy         { dataDir = PathFamilies.synthetic(DATA_ROOT, pkg); ... }
   setupAppRedirections        { PathFamilies.hostForms(EXTERNAL_FILES, pkg).forEach { addPathRedirection(it, ...) } }
```

**共享的是"值"，不是"流程"。**

---

## 十一、这份文档里的十六处「数据重复」与「控制流」对照表

用于评审：看到一个"重复"时，先判断它是数据还是控制流。

| 重复项 | 类型 | 处置 |
| --- | --- | --- |
| `synthetic*` 7 个函数的模板 | **数据** | 可合并进表 |
| 宿主前缀（含/不含尾斜杠） | **数据** | 可合并进表 |
| 沙箱相对位置 | **数据** | 可合并进表 |
| L1 与 L2 各自算一遍合成路径 | **数据 + 控制流混合** | 取值查表，字段赋值保留 |
| `VirtualContext` 13 处 `ensureDir` | **控制流** | 各自保留（可抽辅助函数） |
| `PackageManagerProxy` 反射 `credentialProtectedDataDir` | **控制流** | 保留 |
| 三个 `setup*Redirections` 入口 | **控制流** | 保留 |
| `hiddenPaths` vs `pathRedirections` | **语义不同** | 不合并 |
| Kotlin vs C++ 规则表 | **跨语言** | 不合并（Kotlin 是源，C++ 是同步副本） |
| A 族 / B 族 | **真实差异** | 不合并 |
| `guestExternalData` / `guestExternalDir` | **纯重复** | **已消除**（`04f3c5c`） |
| `pathFidelity` 与 `HOST_EXTERNAL_FALLBACK` 目标相同 | **数据** | 可合并进表（同族两个来源） |
"""

s = s.rstrip() + "\n" + ADD
with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(s)
print("架构文档已加第十、十一节：合并边界判据 + 数据/控制流对照表")
