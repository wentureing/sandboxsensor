"""提取指定类的方法体，并列出它引用的字符串常量，判断是不是「离线列表过滤」。"""
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

print("=== %s 的方法数: %d ===" % (TARGET_CLASS, len(blocks)))
print("  方法名:", [b[0] for b in blocks][:40])
print()

def show(name, body):
    text = "\n".join(body)
    # 字符串常量
    strs = re.findall(r"const-string(?:/jumbo)?\s+v\d+,\s*\"([^\"]*)\"", text)
    # 关键 invoke
    calls = []
    for line in body:
        m = re.search(
            r"invoke-\S+\s+\{[^}]*\},\s*(?://\s*method@[0-9a-f]+\s*)?"
            r"L?([\w/$]+);?\.([\w<>$]+):(\S+)", line)
        if m:
            cls, meth, sig = m.group(1), m.group(2), m.group(3)
            calls.append("%s.%s%s" % (cls.split("/")[-1], meth, sig))
    print("  --- %s ---" % name)
    print("    字符串: %s" % (strs[:12] if strs else "(无)"))
    print("    调用: %s" % (calls[:18] if calls else "(无)"))
    print()

if TARGET_METHOD:
    for n, b in blocks:
        if n == TARGET_METHOD:
            show(n, b)
else:
    # 只展示含 listFiles 或 length 的方法
    for n, b in blocks:
        t = "\n".join(b)
        if "Ljava/io/File;.listFiles" in t or "Ljava/io/File;.length" in t:
            show(n, b)
