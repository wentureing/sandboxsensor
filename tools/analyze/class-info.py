"""打印某个类的继承关系、接口、方法列表（用于从 UI 侧反查数据源）。"""
import re
import sys

PATH = "dist/viadex/dump.txt"
TARGET = sys.argv[1] if len(sys.argv) > 1 else "Leb/s;"

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
SUPER_RE = re.compile(r"^\s*Superclass\s*:\s*'([^']*)'")
IFACE_RE = re.compile(r"^\s*#(\d+)\s*:\s*'([^']*)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")
TYPE_RE = re.compile(r"^\s*type\s*:\s*'([^']+)'\s*$")

in_c = False
superclass = None
interfaces = []
methods = []
name = None
want_type = False
# 收集方法体里引用的关键类型
body_types = set()
cur_body = []

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            if in_c:
                break
            in_c = (cm.group(1) == TARGET)
            continue
        if not in_c:
            continue

        sm = SUPER_RE.match(line)
        if sm:
            superclass = sm.group(1)
            continue

        ifm = IFACE_RE.match(line)
        if ifm and name is None:
            interfaces.append(ifm.group(2))
            continue

        mm = METH_RE.match(line)
        if mm:
            name = mm.group(1)
            want_type = True
            continue

        tm = TYPE_RE.match(line)
        if tm and want_type:
            methods.append((name, tm.group(1)))
            want_type = False
            continue

        # 方法体里出现的类型
        for m in re.finditer(r"L([\w/$]+);", line):
            body_types.add(m.group(1))

print("=== %s ===" % TARGET)
print("  superclass:", superclass)
print("  interfaces:", interfaces if interfaces else "(无)")
print("  方法数:", len(methods))
print()
print("  方法（前 40）:")
for n, t in methods[:40]:
    print("    %-28s %s" % (n, t))
print()
print("  方法体里出现的类型（去重，前 40）:")
for t in sorted(body_types)[:40]:
    print("   ", t)
