"""打印一个类里所有方法的完整字节码（方法少的类用这个，一次看全）。"""
import re
import sys

PATH = "dist/viadex/dump.txt"
TARGET = sys.argv[1] if len(sys.argv) > 1 else "Leb/y;"
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 40

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")
TYPE_RE = re.compile(r"^\s*type\s*:\s*'([^']+)'\s*$")

in_c = False
name = None
sig = None
want_sig = False
cur = []
methods = []

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            if in_c and name:
                methods.append((name, sig, cur))
            in_c = (cm.group(1) == TARGET)
            name = None
            cur = []
            continue
        if not in_c:
            continue

        mm = METH_RE.match(line)
        if mm:
            if name:
                methods.append((name, sig, cur))
            name = mm.group(1)
            sig = None
            cur = []
            want_sig = True
            continue

        tm = TYPE_RE.match(line)
        if tm and want_sig:
            sig = tm.group(1)
            want_sig = False
            continue

        if name:
            cur.append(line)

if in_c and name:
    methods.append((name, sig, cur))

print("=== %s : %d 个方法 ===" % (TARGET, len(methods)))
print()

for n, s, body in methods:
    insns = [l.strip() for l in body if re.match(r"^\S+:\s+[0-9a-f]{4}", l.strip()) or "|" in l and ":" in l]
    # 只打印有 invoke / 关键指令的
    key = [l for l in body if "invoke" in l or "sget" in l or "sput" in l or "iput" in l
           or "iget" in l or "const-string" in l or "return" in l]
    if len(key) == 0:
        continue
    print("--- %s %s ---" % (n, s))
    cnt = 0
    for l in key:
        t = l.strip()
        if not t:
            continue
        # 去掉地址前缀，保留语义
        t2 = re.sub(r"^[0-9a-f]{6}:\s+[0-9a-f ]+\s+", "", t)
        print("    " + t2)
        cnt += 1
        if cnt >= LIMIT:
            print("    ...")
            break
    print()
