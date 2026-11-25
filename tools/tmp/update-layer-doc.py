import io

P = "docs/架构/架构-路径转换分层现状与重复设计.md"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    s = f.read()

OLD = """## 七、尚未查证的两点（诚实标注）

| # | 问题 | 状态 |
| --- | --- | --- |
| 1 | C++ 的 `g_path_redirects` 与 Kotlin 的 `pathRedirections` 是否**始终**同步（有无路径绕过 `nativeAddPathRedirection` 只改 Kotlin 侧） | **未查证** |
| 2 | C++ 的 `/data/user/0/` → `/data/data/` 兜底重试，在 Kotlin 侧没有对应 —— 是否会造成两边行为差异 | **未查证**（Kotlin 侧 `/data/data/` 与 `/data/user/0/` 都注册了规则，所以**当前**不体现差异，但机制上不对称） |"""

NEW = """## 七、两个交叉点的查证结果

### 7.1 规则表是否始终同步：**是**

`NativeHookBridge.kt` 里所有对 `pathRedirections` 的写操作都伴随 native 调用：

| 行 | 操作 | 是否同步 |
| --- | --- | --- |
| L377 | `addPathRedirection` 内调 `nativeAddPathRedirection(from, to)` | ✅ |
| L386-388 | `removePathRedirection` 内调 `nativeRemovePathRedirection(from)` | ✅ |
| L295-296 | `pathRedirections.forEach { nativeAddPathRedirection(...) }` | ✅（批量重放，用于重启后恢复） |
| L396 / L869 | `pathRedirections.clear()` | ⚠️ **没有对应的 native 清空调用** —— 若 native 侧不清，会残留旧规则 |

**结论**：新增/删除都同步；**`clear()` 是个缺口**（只在测试里出现，生产路径未验证）。

### 7.2 `/data/user/0/` → `/data/data/` 兜底：**当前不体现差异，但机制上不对称**

C++ 的 `redirect_path` 有一段 Kotlin 侧没有的兜底：

```cpp
// Special case: /data/user/0/ is the same as /data/data/
if (path_str.compare(0, 13, "/data/user/0/") == 0) {
    std::string alt_path = "/data/data/" + path_str.substr(13);
    return redirect_path(alt_path.c_str());   // 重试匹配
}
```

查证：Kotlin 的 `setupAppRedirections` **同时注册了**两条规则（L412-417）：

```kotlin
addPathRedirection("/data/data/$guestPackageName/", ...)
addPathRedirection("/data/user/0/$guestPackageName/", ...)
```

**所以当前行为一致**。但机制上不对称：**如果将来只注册其中一条**，
C++ 还能靠兜底重试命中，Kotlin 会漏 —— 这类不对称正是"两份实现"的典型代价。"""

assert OLD in s, "第七节未匹配"
s = s.replace(OLD, NEW, 1)

# 更新末尾的一句话，加入 clear() 缺口
OLD2 = """其中「两套规则表」已经实证过一次脱节（Kotlin 有 `String.replace` bug 而 C++ 没有）。**"""
NEW2 = """其中「两套规则表」已经实证过一次脱节（Kotlin 有 `String.replace` bug 而 C++ 没有），
> 另有一个未验证缺口（`pathRedirections.clear()` 没有对应的 native 清空）。**"""
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(s)
print("文档已更新：两个交叉点已查证")
