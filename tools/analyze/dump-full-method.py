"""打印 ha/e.f 的完整字节码（含寄存器与 invoke），定位每个丢弃分支。"""
import re
import sys

PATH = "dist/viadex/dump.txt"
TARGET_CLASS = sys.argv[1] if len(sys.argv) > 1 else "Lha/e;"
TARGET_METHOD = sys.argv[2] if len(sys.argv) > 2 else "f"
LIMIT = int(sys.argv[3]) if len(sys.argv) > 3 else 120

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")
TYPE_RE = re.compile(r"^\s*type\s*:\s*'([^']+)'\s*$")

in_class = False
cur_method = None
cur_type = None
out = []
capture = False

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            if capture and out:
                break
            in_class = (cm.group(1) == TARGET_CLASS)
            cur_method = None
            continue
        if not in_class:
            continue
        mm = METH_RE.match(line)
        if mm:
            if capture and out:
                break
            cur_method = mm.group(1)
            capture = (cur_method == TARGET_METHOD)
            continue
        tm = TYPE_RE.match(line)
        if tm and capture and not out:
            cur_type = tm.group(1)
            continue
        if capture:
            out.append(line)

print("=== %s.%s  签名 %s ===" % (TARGET_CLASS, TARGET_METHOD, cur_type))
print()

n = 0
for line in out:
    s = line.strip()
    if not s or s.startswith("|") is False and ":" not in s:
        continue
    # 只保留指令行与 invoke 注释
    if re.match(r"^[0-9a-f]{6}:", s) or s.startswith("|"):
        print("  " + s)
        n += 1
    elif "//" in s and ("invoke" in s or "class@" in s or "field@" in s):
        print("  " + s)
        n += 1
    if n > LIMIT:
        print("  ...")
        break
