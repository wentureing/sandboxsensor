"""找 ha/e.f 的调用方，以及 ha/d 的 length 字段读取方。"""
import re

PATH = "dist/viadex/dump.txt"

# 要搜的签名片段
PATTERNS = [
    (r"Lha/e;\.f:\(\)", "ha/e.f()"),
    (r"Lha/e;\.f:\([^)]*\)", "ha/e.f(...)"),
    (r"Lha/e;\.e:\([^)]*\)", "ha/e.e(...)"),
]

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")

cur_class = "?"
cur_method = None
hits = {p[1]: [] for p in PATTERNS}

# 先看 ha/d 的字段与 setter/getter
d_fields = []
d_methods = []

in_ha_e = False
in_ha_d = False

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            cur_class = cm.group(1)
            cur_method = None
            in_ha_e = cur_class.startswith("Lha/e")
            in_ha_d = cur_class.startswith("Lha/d")
            continue
        mm = METH_RE.match(line)
        if mm:
            cur_method = mm.group(1)
            if in_ha_d:
                d_methods.append(cur_method)
            continue

        for pat, label in PATTERNS:
            if re.search(pat, line) and "invoke" in line:
                hits[label].append((cur_class.strip("L;"), cur_method, line.strip()))

print("=== ha/e 的方法调用点 ===")
for label, items in hits.items():
    print("\n  %s : %d 处" % (label, len(items)))
    seen = set()
    for cls, meth, ln in items:
        key = (cls, meth)
        if key in seen:
            continue
        seen.add(key)
        print("    %s.%s" % (cls, meth))

print()
print("=== ha/d 的方法列表（length/mtime 的 setter/getter 在这里）===")
print("  ", d_methods)
