"""多源多代理尝试下载 smali 工具。先探哪个组合能通。"""
import os
import ssl
import urllib.request

OUT = "tools/pylib"
os.makedirs(OUT, exist_ok=True)

PROXIES = ["http://127.0.0.1:7897", "http://127.0.0.1:12450", None]

MIRRORS = [
    ("aliyun", "https://maven.aliyun.com/repository/public/com/android/tools/smali/{n}/3.0.5/{n}-3.0.5-fat.jar"),
    ("central", "https://repo1.maven.org/maven2/com/android/tools/smali/{n}/3.0.5/{n}-3.0.5-fat.jar"),
]

NAMES = ["baksmali", "smali"]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 只探测 baksmali，找到可用组合后照做
def try_url(url, proxy):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    try:
        with opener.open(url, timeout=90) as r:
            return r.read()
    except Exception as e:
        return "ERR:%s: %s" % (type(e).__name__, e)


found = None
for mname, tmpl in MIRRORS:
    url = tmpl.format(n="baksmali")
    for proxy in PROXIES:
        tag = "%s / proxy=%s" % (mname, proxy)
        print("试 %s ..." % tag, flush=True)
        r = try_url(url, proxy)
        if isinstance(r, bytes) and len(r) > 100000:
            print("  OK  %d bytes" % len(r), flush=True)
            found = (tmpl, proxy)
            with open(os.path.join(OUT, "baksmali.jar"), "wb") as f:
                f.write(r)
            break
        else:
            print("  %s" % (r if isinstance(r, str) else "too small"), flush=True)
    if found:
        break

if not found:
    print("\n所有组合都失败")
    raise SystemExit(1)

tmpl, proxy = found
for n in NAMES:
    dst = os.path.join(OUT, n + ".jar")
    if os.path.exists(dst) and os.path.getsize(dst) > 100000:
        print("已存在:", n)
        continue
    url = tmpl.format(n=n)
    print("下载 %s ..." % n, flush=True)
    r = try_url(url, proxy)
    if isinstance(r, bytes) and len(r) > 100000:
        with open(dst, "wb") as f:
            f.write(r)
        print("  OK %d bytes" % len(r), flush=True)
    else:
        print("  FAIL %s" % r, flush=True)

print()
for n in NAMES:
    p = os.path.join(OUT, n + ".jar")
    if os.path.exists(p):
        print("  %s  %d bytes" % (p, os.path.getsize(p)))
