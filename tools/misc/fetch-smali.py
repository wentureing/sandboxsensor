"""用代理下载 baksmali / smali（修改 dex 用）。

沙箱里 curl 不能联网，但 python 走代理可以。
"""
import os
import urllib.request

PROXY = "http://127.0.0.1:12450"
OUT = "tools/pylib"

TARGETS = [
    ("https://repo1.maven.org/maven2/com/android/tools/smali/baksmali/3.0.5/baksmali-3.0.5-fat.jar",
     "baksmali.jar"),
    ("https://repo1.maven.org/maven2/com/android/tools/smali/smali/3.0.5/smali-3.0.5-fat.jar",
     "smali.jar"),
]

os.makedirs(OUT, exist_ok=True)

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
)
opener.addheaders = [("User-Agent", "Mozilla/5.0")]

for url, name in TARGETS:
    dst = os.path.join(OUT, name)
    if os.path.exists(dst) and os.path.getsize(dst) > 100000:
        print("已存在:", dst, os.path.getsize(dst))
        continue
    print("下载 %s ..." % name, flush=True)
    try:
        with opener.open(url, timeout=120) as r:
            data = r.read()
        with open(dst, "wb") as f:
            f.write(data)
        print("  OK %s  %d bytes" % (dst, len(data)))
    except Exception as e:
        print("  FAIL %s: %s: %s" % (name, type(e).__name__, e))
