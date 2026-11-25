import re

P = "dist/viadex/dump.txt"
TARGET = "Leb/s;"

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")
TYPE_RE = re.compile(r"^\s*type\s*:\s*'([^']+)'\s*$")

in_c = False
name = None
want = False
out = []

with open(P, encoding="utf-8", errors="replace") as f:
    for line in f:
        m = CLASS_RE.match(line)
        if m:
            in_c = (m.group(1) == TARGET)
            name = None
            want = False
            continue
        if not in_c:
            continue
        m = METH_RE.match(line)
        if m:
            name = m.group(1)
            want = True
            continue
        m = TYPE_RE.match(line)
        if m and want:
            out.append((name, m.group(1)))
            want = False

print("total methods:", len(out))
print()
print("=== 第 40 个之后的方法（含 V1/M1）===")
for i, (n, t) in enumerate(out):
    if i >= 38:
        print("  [%2d] %-18s %s" % (i, n, t))
print()
print("=== 名字以 V/M/W/U 开头的方法 ===")
for n, t in out:
    if n[:1] in ("V", "M", "W", "U"):
        print("  %-18s %s" % (n, t))
