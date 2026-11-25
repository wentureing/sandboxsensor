"""找 ha/e.b()Ljava/util/List; 的调用方，以及谁 new-instance ha/e。"""
import re

PATH = "dist/viadex/dump.txt"

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")
TYPE_RE = re.compile(r"^\s*type\s*:\s*'([^']+)'\s*$")

TARGETS = [
    (r"Lha/e;\.b:\(\)Ljava/util/List;", "ha/e.b() -> List"),
    (r"Lha/e;\.<init>:\(Landroid/content/Context;\)V", "new ha/e(Context)"),
    (r"Lha/e;\.a:\(Ln9/d;\)Z", "ha/e.a(Item) -> Z  [删除项]"),
    (r"Ln9/d;", "引用 n9/d（条目类型）"),
]

cur_class = "?"
cur_method = None
hits = {label: [] for _, label in TARGETS}

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            cur_class = cm.group(1)
            cur_method = None
            continue
        mm = METH_RE.match(line)
        if mm:
            cur_method = mm.group(1)
            continue
        if "invoke" not in line and "new-instance" not in line and "sget" not in line:
            continue
        for pat, label in TARGETS:
            if re.search(pat, line):
                hits[label].append((cur_class.strip("L;"), cur_method, line.strip()))

for _, label in TARGETS:
    items = hits[label]
    print("=== %s : %d 处 ===" % (label, len(items)))
    seen = {}
    for cls, meth, ln in items:
        key = (cls, meth)
        seen[key] = seen.get(key, 0) + 1
    for (cls, meth), cnt in sorted(seen.items(), key=lambda x: -x[1])[:15]:
        print("    %s.%s  (%d 次)" % (cls, meth, cnt))
    print()
