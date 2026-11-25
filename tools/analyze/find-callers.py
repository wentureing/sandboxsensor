"""通用：找某个方法（类+方法名+签名）的所有调用方。

用法：
    python tools/analyze/find-callers.py "Leb/s;" "S2" "(Ljava/util/List;)V"
不传签名则匹配所有同名方法。
"""
import re
import sys

PATH = "dist/viadex/dump.txt"
TARGET_CLASS = sys.argv[1]
TARGET_METHOD = sys.argv[2]
TARGET_SIG = sys.argv[3] if len(sys.argv) > 3 else None

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")

# 拼出要匹配的引用形式：L<class>;.<method>:<sig>
want_prefix = "%s.%s:" % (TARGET_CLASS, TARGET_METHOD)

cur_class = "?"
cur_method = None
hits = []

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            cur_class = cm.group(1)
            cur_method = None
            continue
        mm = METH_RE.match(line)
        if mm:
            cur_method = mm.group(1)
            continue
        if "invoke" not in line:
            continue
        if want_prefix not in line:
            continue
        if TARGET_SIG is not None and TARGET_SIG not in line:
            continue
        hits.append((cur_class.strip("L;"), cur_method, line.strip()))

print("=== %s.%s%s 的调用方：%d 处 ===" % (
    TARGET_CLASS, TARGET_METHOD,
    (" " + TARGET_SIG) if TARGET_SIG else "", len(hits)))
print()
summary = {}
for cls, meth, ln in hits:
    key = (cls, meth)
    summary[key] = summary.get(key, 0) + 1
for (cls, meth), cnt in sorted(summary.items(), key=lambda x: -x[1]):
    print("  %s.%s   (%d 次)" % (cls, meth, cnt))
print()
if hits:
    print("=== 原始行（前 5 条）===")
    for cls, meth, ln in hits[:5]:
        print("  [%s.%s] %s" % (cls, meth, ln))
