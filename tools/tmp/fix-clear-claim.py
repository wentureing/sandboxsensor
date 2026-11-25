import io

P = "docs/架构/架构-路径转换分层现状与重复设计.md"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    s = f.read()

OLD = """| L396 / L869 | `pathRedirections.clear()` | ⚠️ **没有对应的 native 清空调用** —— 若 native 侧不清，会残留旧规则 |

**结论**：新增/删除都同步；**`clear()` 是个缺口**（只在测试里出现，生产路径未验证）。"""

NEW = """| L395-399 | `clearPathRedirections()` 内调 `nativeClearPathRedirections()` | ✅（native 侧 L2410 有实现，`g_path_redirects.clear()`） |

**结论：增/删/清空三条路径都同步。** 这一项原判为「缺口」，是**误判** —— 我只看了
`pathRedirections.clear()` 这一行，没往下看三行的 `nativeClearPathRedirections()`。

> 这是本轮**第三次**「只看了半截就下结论」。前两次是 `getFileStreamPath`（实际自动跟随）
> 和 `dataDir`（实际 `PackageManagerProxy` 已合成）。
> **教训：判断"某能力有没有"，必须读完整个函数，不能只看第一行。**"""

assert OLD in s, "缺口段未匹配"
s = s.replace(OLD, NEW, 1)

OLD2 = """> 另有一个未验证缺口（`pathRedirections.clear()` 没有对应的 native 清空）。"""
NEW2 = """> 规则表的增/删/清空三条路径都同步（已查证）。"""
if OLD2 in s:
    s = s.replace(OLD2, NEW2, 1)

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(s)
print("已更正 clear() 的误判")
