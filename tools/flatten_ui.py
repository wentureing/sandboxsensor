"""批量「去装饰」：把圆角与阴影换成直角/无阴影。

策略（不改调用结构，只换 Shape/删 shadow）：
  1. RoundedCornerShape(...)  -> RectangleShape
  2. Modifier.shadow(...)     -> （删除）

用法：python tools/flatten_ui.py [--dry-run]
"""
import io
import re
import sys
import glob

ROOT = r"C:\Users\86177\Desktop\轻简虚拟环境开发\_ref\ShitJumpingVM"
DRY = "--dry-run" in sys.argv

FILES = (
    glob.glob(ROOT + r"\feature\**\*.kt", recursive=True)
    + glob.glob(ROOT + r"\app\src\main\java\**\*.kt", recursive=True)
)

ROUND = re.compile(r"RoundedCornerShape\(\s*[^()]*\)")
SHADOW = re.compile(r"\.shadow\(\s*[^()]*\)")

total_round = 0
total_shadow = 0
changed = []

for p in FILES:
    s = io.open(p, encoding="utf-8").read()
    o = s

    n_round = len(ROUND.findall(s))
    s = ROUND.sub("RectangleShape", s)

    n_shadow = len(SHADOW.findall(s))
    s = SHADOW.sub("", s)

    if s == o:
        continue

    if n_round and "import androidx.compose.foundation.shape.RectangleShape" not in s:
        s = s.replace(
            "import androidx.compose.foundation.shape.RoundedCornerShape",
            "import androidx.compose.foundation.shape.RectangleShape",
            1,
        )
        if "import androidx.compose.foundation.shape.RectangleShape" not in s:
            # 没有 RoundedCornerShape import 可换，插到第一个 import 之后
            m = re.search(r"^import .+$", s, re.M)
            if m:
                s = s[: m.end()] + "\nimport androidx.compose.foundation.shape.RectangleShape" + s[m.end():]

    if not DRY:
        io.open(p, "w", encoding="utf-8", newline="").write(s)
    total_round += n_round
    total_shadow += n_shadow
    changed.append((p.split("\\")[-1], n_round, n_shadow))

print(f"{'[dry-run] ' if DRY else ''}改动文件 {len(changed)} 个")
for name, r, sh in sorted(changed, key=lambda x: -(x[1] + x[2]))[:12]:
    print(f"  {name:<32} 圆角 {r}  阴影 {sh}")
print(f"合计：圆角 {total_round} 处，阴影 {total_shadow} 处")
