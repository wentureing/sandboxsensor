"""在 via 的反编译输出里找「拿 listFiles 结果后按 isFile/length 过滤」的具体方法。

判据：同一个方法体内同时出现
    invoke-*  Ljava/io/File;.listFiles(...)
  以及至少一个
    invoke-*  Ljava/io/File;.(isFile|length|exists|lastModified)(...)
就把它列为候选过滤点，并打印它的类名/方法名 + 相关 invoke 序列。
"""
import re
import sys

PATH = "dist/viadex/dump.txt"

CLASS_RE = re.compile(r"^\s*Class descriptor\s*:\s*'([^']+)'")
METH_RE = re.compile(r"^\s*name\s*:\s*'([^']+)'\s*$")
TYPE_RE = re.compile(r"^\s*type\s*:\s*'([^']+)'\s*$")

INTEREST = re.compile(r"Ljava/io/File;\.(listFiles|list|isFile|isDirectory|length|exists|lastModified|getName)\b")

cur_class = "?"
cur_method = None
buf = []
results = []

def flush():
    global buf, cur_method
    if cur_method is None or not buf:
        buf = []
        return
    text = "\n".join(buf)
    has_list = "Ljava/io/File;.listFiles" in text
    checks = set(re.findall(r"Ljava/io/File;\.(isFile|length|exists|lastModified|isDirectory|getName)", text))
    if has_list:
        seq = []
        for line in buf:
            m = INTEREST.search(line)
            if m:
                seq.append(m.group(1))
        results.append((cur_class, cur_method, sorted(checks), seq))
    buf = []

with open(PATH, "r", encoding="utf-8", errors="replace") as f:
    for line in f:
        cm = CLASS_RE.match(line)
        if cm:
            flush()
            cur_class = cm.group(1)
            cur_method = None
            continue
        mm = METH_RE.match(line)
        if mm:
            flush()
            cur_method = mm.group(1)
            continue
        if cur_method is not None:
            buf.append(line)

flush()

print("=== 含 File.listFiles 的方法：%d 个 ===" % len(results))
print()

# 优先展示「同时做了 isFile/length/exists 过滤」的
filtered = [r for r in results if r[2]]
print("=== 其中做了进一步判断的：%d 个 ===\n" % len(filtered))

# 按判断的丰富度排序
filtered.sort(key=lambda r: -len(r[2]))
for cls, meth, checks, seq in filtered[:15]:
    cls_s = cls.strip("L;")
    print("  %s.%s" % (cls_s, meth))
    print("      判断项: %s" % ", ".join(checks))
    print("      调用序列: %s" % " -> ".join(seq[:12]))
    print()

if not filtered:
    print("（没有方法在 listFiles 之后直接做判断 —— 说明过滤可能发生在调用方，")
    print("  需要按「返回 String[]/List 的方法」再往上追一层）")

# 列出所有含 listFiles 的方法名，便于人工定位
print("=== 所有含 listFiles 的方法（类名.方法名）===")
seen = set()
for cls, meth, checks, seq in results:
    key = (cls.strip("L;"), meth)
    if key in seen:
        continue
    seen.add(key)
    print("  %s.%s" % key)
