"""按「方法名 + 签名」精确提取一个方法的完整字节码。"""
import re
import sys

PATH = "dist/viadex/dump.txt"
TARGET_CLASS = sys.argv[1]
TARGET_METHOD = sys.argv[2]
TARGET_SIG = sys.argv[3] if len(sys.argv) > 3 else None
LIMIT = int(sys.argv[4]) if len(sys.argv) > 4 else 90

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")
TYPE_RE = re.compile(r"^\s*type\s*:\s*'([^']+)'\s*$")

in_class = False
pending_name = None
state = "idle"        # idle -> got_name -> got_type -> capture
cur_name = None
out = []
results = []

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            if state == "capture":
                results.append((cur_name, out))
            state = "idle"
            in_class = (cm.group(1) == TARGET_CLASS)
            continue
        if not in_class:
            continue

        mm = METH_RE.match(line)
        if mm:
            if state == "capture":
                results.append((cur_name, out))
            pending_name = mm.group(1)
            state = "got_name"
            out = []
            continue

        tm = TYPE_RE.match(line)
        if tm and state == "got_name":
            sig = tm.group(1)
            if pending_name == TARGET_METHOD and (TARGET_SIG is None or sig == TARGET_SIG):
                cur_name = "%s %s" % (pending_name, sig)
                state = "capture"
            else:
                state = "idle"
            continue

        if state == "capture":
            out.append(line)

    if state == "capture":
        results.append((cur_name, out))

if not results:
    print("未找到 %s.%s %s" % (TARGET_CLASS, TARGET_METHOD, TARGET_SIG))
    sys.exit(1)

for name, body in results:
    print("=== %s.%s ===" % (TARGET_CLASS, name))
    print()
    n = 0
    for line in body:
        s = line.strip()
        if not s:
            continue
        if re.match(r"^[0-9a-f]{6}:", s) or s.startswith("|") or ("//" in s and ("invoke" in s or "class@" in s or "field@" in s or "string@" in s)):
            print("  " + s)
            n += 1
        if n > LIMIT:
            print("  ...")
            break
    print()
