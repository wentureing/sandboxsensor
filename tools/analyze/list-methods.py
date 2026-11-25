import re

PATH = "dist/viadex/dump.txt"
TARGET = "Lha/e;"

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")
TYPE_RE = re.compile(r"^\s*type\s*:\s*'([^']+)'\s*$")

in_c = False
name = None
want = False

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
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
            print("  %-30s %s" % (name, m.group(1)))
            want = False
