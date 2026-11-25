"""提取指定方法里的比较/跳转指令，用于定位「某字段为 0 就丢弃」这类过滤。"""
import re
import sys

PATH = "dist/viadex/dump.txt"
TARGET_CLASS = sys.argv[1] if len(sys.argv) > 1 else "Lha/e;"
TARGET_METHOD = sys.argv[2] if len(sys.argv) > 2 else None

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")

in_class = False
cur_method = None
buf = []
blocks = []

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            if cur_method is not None:
                blocks.append((cur_method, buf))
                cur_method, buf = None, []
            in_class = (cm.group(1) == TARGET_CLASS)
            continue
        if not in_class:
            continue
        mm = METH_RE.match(line)
        if mm:
            if cur_method is not None:
                blocks.append((cur_method, buf))
            cur_method = mm.group(1)
            buf = []
            continue
        if cur_method is not None:
            buf.append(line)
    if cur_method is not None:
        blocks.append((cur_method, buf))

JMP = re.compile(r"\b(if-[a-z]+|cmp-long|cmpl-[a-z]+|cmpg-[a-z]+|cmp-long)\b")


def show(name, body):
    print("  --- %s ---" % name)
    n = 0
    for line in body:
        code = re.sub(r"//.*$", "", line).rstrip()
        if JMP.search(code) or "const/4" in code or "const-wide/16" in code or "const/16" in code:
            s = code.strip()
            if not s:
                continue
            print("     " + s)
            n += 1
            if n > 40:
                print("     ...")
                break
    print()


if TARGET_METHOD:
    for nm, b in blocks:
        if nm == TARGET_METHOD:
            show(nm, b)
else:
    for nm, b in blocks:
        t = "\n".join(b)
        if "Cursor.getLong" in t or "File.length" in t:
            show(nm, b)
