import io

P = "docs/计划/计划-路径转换重构-表驱动精简.md"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    s = f.read()
nl = "\r\n" if "\r\n" in s else "\n"

if "不要为了合并而合并" in s:
    print("已存在")
    raise SystemExit(0)

# 在文档开头插入一节「边界修正」，并替换第三节的过度合并方案
ANCHOR = "## 三、重构方案：一张表驱动"
assert ANCHOR in s, "第三节未找到"

NEW_SECTION = """## 二·五、边界修正：不要为了合并而合并

**用户明确要求**：「不要为了合并而合并，允许使用配置表 + 各自独立的使用方式，
对于功能差异大的不要合并。」

### 我原来的方案过度了

原第三节的方案是「三处消费方全部查表」—— 这等于把**数据**和**控制流**一起合并了。
实际这三处消费方的**使用逻辑差异很大**：

| 消费方 | 除了"知道假路径是什么"，还要做什么 |
| --- | --- |
| `VirtualContext.getFilesDir()` 等 13 处 | **先 `sandboxManager.ensureDir(...)` 建沙箱目录**，再返回假路径 |
| `PackageManagerProxy` | **设置 `ApplicationInfo` 的不同字段**（`dataDir` / `sourceDir` / `nativeLibraryDir`…），还要反射设 `credentialProtectedDataDir` |
| `setupAppRedirections` | **注册规则**（`addPathRedirection`），且**时机**与上面两处不同（guest bind 时） |

**这三件事的共同点只有"假路径模板"，其余全是差异。**
把这些差异硬塞进一个表，等于把三套控制流挤成一套 —— 那不是精简，是耦合。

### 修正后的边界

| 层 | 是否合并 | 理由 |
| --- | --- | --- |
| **`synthetic*` 的假路径模板**（7 个函数） | ✅ **合并成配置表** | **纯数据**，无副作用、无控制流 |
| **沙箱相对位置**（`files` / `sdcard/Android/data/<pkg>/files` …） | ✅ **合并进同一张表** | 同上，纯数据 |
| **宿主前缀变体**（含/不含尾斜杠） | ✅ **合并进同一张表** | 同上，纯数据 |
| `VirtualContext` 的 13 处分支 | ❌ **各自保留** | 每处都有 `ensureDir` 副作用；**但可以抽一个辅助函数**减少重复，不强行查表 |
| `PackageManagerProxy` 的字段设置 | ❌ **各自保留** | 设的是不同字段，反射逻辑只属于它 |
| 三个 `setup*Redirections` 入口 | ❌ **各自保留** | 注册时机与组合方式不同 |
| `hiddenPaths` | ❌ **不合并** | 精确匹配，与规则的前缀语义相反 |
| C++ `g_path_redirects` | ❌ **不合并** | 跨语言；且它是同步副本，不是独立知识 |
| A 族 / B 族失败契约 | ❌ **不合并** | Android 本身的真实差异 |

### 一句话

> **表只承载「数据」，不承载「控制流」。**
> 假路径长什么样、沙箱落到哪、要劫持哪些宿主前缀 —— 这三样是数据，进表。
> 谁在什么时候用、要不要先建目录、设哪个字段 —— 这些是控制流，各自保留。

"""

s = s.replace(ANCHOR, NEW_SECTION + "---" + nl + nl + ANCHOR, 1)

# 替换第三节的实现描述（把"三处消费方全部查表"改成数据表 + 各自独立用法）
OLD_32 = """### 3.2 三处消费方都改成"查表"

| 消费方 | 改法 |
| --- | --- |
| **`VirtualContext`** | 13 处分支 → 一个 `syntheticPath(family, pkg, type)` 辅助函数，内部查表 |
| **`PackageManagerProxy`** | 4 个字段 → 循环表设置（`dataDir` = `DATA_ROOT`、`sourceDir` = `APK`…） |
| **`setupAppRedirections`** | 20+ 条手写规则 → `PATH_FAMILIES.forEach { it.hostForms(pkg).forEach { h -> addPathRedirection(h, <sandbox>) } }` |"""

NEW_32 = """### 3.2 消费方各自保留逻辑，只共享"查数据"

| 消费方 | 改法 | 保留什么 |
| --- | --- | --- |
| **`VirtualContext`** | 保留 13 处分支（各有 `ensureDir`），**但把"取假路径"改成查表**：`PathFamilies.synthetic(FILES, pkg)` | **保留 `ensureDir` 的顺序与副作用** |
| **`PackageManagerProxy`** | 保留字段赋值与反射，只把"取假路径"改成查表 | **保留反射 `credentialProtectedDataDir`** |
| **`setupAppRedirections`** | 规则仍由它自己 `addPathRedirection` 注册，只把"宿主前缀列表"改成查表 | **保留注册时机与三个入口的分工** |

**判据：只把"常量/模板/前缀列表"换成查表；凡是带副作用的语句（`ensureDir`、`mkdirs`、
反射、`addPathRedirection`）都留在原处。**"""

if OLD_32 in s:
    s = s.replace(OLD_32, NEW_32, 1)

# 替换 3.3 标题下的"结构上不可能漏"表述（避免再次夸大）
s = s.replace(
    "| T39 事后检查\"合成路径都有规则\" | **结构上不可能漏** —— 规则由表生成 |",
    "| T39 事后检查\"合成路径都有规则\" | **表里一行写全** —— 少写 `hostForms` 会被表结构强制（`PathFamilySpec` 三个字段都必填） |",
    1)

# 在第五节「不做什么」补充一条
OLD_5 = "| 不动 C++ 的 `g_path_redirects` | 它与 Kotlin 通过 `nativeAddPathRedirection` 同步；S2 做完后 C++ 侧自动得到同样的规则，不需要改 C++ |"
NEW_5 = OLD_5 + "\n| **不把消费方的控制流合并** | 见「二·五」：`ensureDir` / 反射 / `addPathRedirection` 都留在原处 |\n| **不合并 `hiddenPaths`** | 精确匹配，与规则的前缀语义相反 |"
if OLD_5 in s:
    s = s.replace(OLD_5, NEW_5, 1)

# 更新执行顺序的措辞
s = s.replace(
    "| **S3** | `VirtualContext` 的 13 处分支改成查表 | T39-T41 全绿 + 真机验证 via 可用 | 中 |",
    "| **S3** | `VirtualContext` 的 13 处分支：**只把取假路径换成查表**，分支与 `ensureDir` 保留 | T39-T41 全绿 + 真机验证 via 可用 | 低~中 |",
    1)
s = s.replace(
    "| **S4** | `PackageManagerProxy` 改成查表 | 同上 | 中 |",
    "| **S4** | `PackageManagerProxy`：**只把取假路径换成查表**，字段赋值与反射保留 | 同上 | 低 |",
    1)

# 末尾一句话更新
s = s.replace(
    "> 让\"加一个路径族\"只需要改表里的一行。**",
    "> 让\"加一个路径族\"只需要改表里的一行。**\n>\n> **但表只承载数据：假路径模板、沙箱相对位置、宿主前缀变体。\n> 谁在什么时候用、要不要先建目录、设哪个字段 —— 这些控制流各自保留，不强行合并。**",
    1)

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(s)
print("计划文档已修正：表只承载数据，控制流各自保留")
