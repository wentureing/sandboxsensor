"""用代理 + 不验证证书下载 smali 工具（沙箱里证书路径缺失，必须跳过验证）。"""
import os
import ssl
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

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}),
    urllib.request.HTTPSHandler(context=ctx),
)
opener.addheaders = [("User-Agent", "Mozilla/5.0")]

for url, name in TARGETS:
    dst = os.path.join(OUT, name)
    if os.path.exists(dst) and os.path.getsize(dst) > 100000:
        print("已存在:", dst, os.path.getsize(dst), flush=True)
        continue
    print("下载 %s ..." % name, flush=True)
    try:
        with opener.open(url, timeout=180) as r:
            data = r.read()
        with open(dst, "wb") as f:
            f.write(data)
        print("  OK %s  %d bytes" % (dst, len(data)), flush=True)
    except Exception as e:
        print("  FAIL %s: %s: %s" % (name, type(e).__name__, e), flush=True)
