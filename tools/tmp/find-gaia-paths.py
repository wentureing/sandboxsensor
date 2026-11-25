"""在 Gaia 的 dex 里找路径模板（"gaia/" 的完整形式）。

dex 里 "gaia/" 出现 2557 次，说明模板在 Java 层。
目标：找到类似 "/data/user/0/<host>/gaia/data/user/0/" 的构造方式。
"""
import re
import zipfile

z = zipfile.ZipFile("dist/gaia.apk")
dexes = [n for n in z.namelist() if n.endswith(".dex")]

# dex 字符串池是 MUTF-8，连续可读串里找含 gaia 的
PAT = re.compile(rb"[ -~]{8,}")

for n in dexes:
    data = z.read(n)
    found = set()
    for m in PAT.finditer(data):
        s = m.group(0)
        if b"gaia" in s.lower() or b"/data/user" in s or b"sdcard/Android" in s:
            t = s.decode("ascii", "replace")
            # 过滤明显无关的
            if len(t) > 12:
                found.add(t)
    if not found:
        continue
    print("=" * 70)
    print("=== %s ===" % n)
    # 先打印含 /data/user 或 sdcard 的（最重要）
    key = [t for t in found if "/data/user" in t or "sdcard/Android" in t]
    for t in sorted(key)[:15]:
        print("  [PATH] ", t[:120])
    print()
    other = [t for t in found if "gaia" in t.lower() and t not in key]
    for t in sorted(other)[:18]:
        print("  [GAIA] ", t[:120])
    print()
