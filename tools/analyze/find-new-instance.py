"""找 new-instance <类> 出现的宿主方法，以及附近 observe() 调用的对象来源。"""
import re
import sys

PATH = "dist/viadex/dump.txt"
TARGET_TYPE = sys.argv[1] if len(sys.argv) > 1 else "Leb/b;"

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")
TYPE_RE = re.compile(r"^\s*type\s*:\s*'([^']+)'\s*$")

cur_class = "?"
cur_method = None
cur_sig = None
want_sig = False
ctx = []          # 当前方法的所有行
hits = []
NEW_RE = re.compile(r"new-instance\s+v\d+,\s*" + re.escape(TARGET_TYPE))

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            if cur_method and any(NEW_RE.search(x) for x in ctx):
                hits.append((cur_class, cur_method, cur_sig, list(ctx)))
            cur_class = cm.group(1)
            cur_method = None
            cur_sig = None
            ctx = []
            continue
        mm = METH_RE.match(line)
        if mm:
            if cur_method and any(NEW_RE.search(x) for x in ctx):
                hits.append((cur_class, cur_method, cur_sig, list(ctx)))
            cur_method = mm.group(1)
            want_sig = True
            ctx = []
            continue
        tm = TYPE_RE.match(line)
        if tm and want_sig:
            cur_sig = tm.group(1)
            want_sig = False
            continue
        if cur_method:
            ctx.append(line)

if cur_method and any(NEW_RE.search(x) for x in ctx):
    hits.append((cur_class, cur_method, cur_sig, list(ctx)))

print("=== new-instance %s 出现在 %d 个方法里 ===" % (TARGET_TYPE, len(hits)))
print()
for cls, meth, sig, body in hits:
    print("  %s.%s %s" % (cls.strip("L;"), meth, sig))
    # 打印该方法里所有 invoke
    for line in body:
        s = line.strip()
        if "invoke" in s and "//" in s:
            print("      " + s)
    print()
