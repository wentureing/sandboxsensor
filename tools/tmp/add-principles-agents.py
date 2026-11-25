import io

P = "AGENTS.md"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    s = f.read()
nl = "\r\n" if "\r\n" in s else "\n"

if "设计-项目设计理念.md" in s:
    print("已存在")
    raise SystemExit(0)

anchor = "## 路径合成：syntheticDataDir / pathFidelity"
assert anchor in s, "anchor 未找到"

BLOCK = nl.join([
    "## 设计理念（改动前第一道尺子）",
    "",
    "> **`docs/设计/设计-项目设计理念.md`** —— 三条理念，与任何改动冲突时，要么改改动，要么先改这份文档。",
    "",
    "1. **有条理、技术易实现** —— 三源交叉（AOSP 源码看行为 / 设备 dex 看谁真 native / 日志看谁没生效）、",
    "   契约驱动（先列调用方假设）、先写测试再改、每层独立计数。",
    "2. **充分利用上游代码** —— 先问「上游有没有现成实现」。上游的 **bug 修参数**，上游的 **机制保留**。",
    "   反例：看到 `external_data`/`obb` 目标错就想全部重写，其实规则映射机制是对的。",
    "3. **路径欺骗：看起来真实、用起来重定向、骗不过就放弃** ——",
    "   需要骗的（含包名的 Context 路径）合成；不需要骗的（`Environment`/`MediaStore`/`StorageVolume`，",
    "   本来就不含包名）一律不改。**硬约束：只误导，不危害读写** —— 宁可不骗，也不能把 guest 弄坏。",
    "",
])

s = s.replace(anchor, BLOCK + anchor, 1)
with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(s)
print("AGENTS.md 已插入设计理念一节")
