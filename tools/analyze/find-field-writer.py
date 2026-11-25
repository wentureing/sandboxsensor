"""找对某个字段（class.field:type）做 iput-object 的位置。"""
import re
import sys

PATH = "dist/viadex/dump.txt"
FIELD = sys.argv[1] if len(sys.argv) > 1 else "Leb/s;.B0:Leb/y;"

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")
TYPE_RE = re.compile(r"^\s*type\s*:\s*'([^']+)'\s*$")

cur_class = "?"
cur_method = None
cur_sig = None
want_sig = False
hits = []
ctx = []

with open(PATH, encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            cur_class = cm.group(1)
            cur_method = None
            ctx = []
            continue
        mm = METH_RE.match(line)
        if mm:
            cur_method = mm.group(1)
            want_sig = True
            ctx = []
            continue
        tm = TYPE_RE.match(line)
        if tm and want_sig:
            cur_sig = tm.group(1)
            want_sig = False
            continue
        if cur_method and FIELD in line and ("iput" in line or "sput" in line):
            # 打印前后几行作为上下文
            hits.append((cur_class.strip("L;"), cur_method, cur_sig, line.strip()))

print("=== 对 %s 赋值的点：%d 处 ===" % (FIELD, len(hits)))
print()
for cls, meth, sig, ln in hits:
    print("  %s.%s %s" % (cls, meth, sig))
    print("      " + re.sub(r"^[0-9a-f]{6}:\s+[0-9a-f ]+\s+", "", ln))
    print()
