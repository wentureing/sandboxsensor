"""华为云 Maven 镜像 + 固定 12450 代理，探测并下载 baksmali/smali。"""
import os
import re
import ssl
import urllib.request

PROXY = "http://127.0.0.1:12450"
BASE = "https://repo.huaweicloud.com/repository/maven"
OUT = "tools/pylib"
os.makedirs(OUT, exist_ok=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def get(url, timeout=90):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}),
        urllib.request.HTTPSHandler(context=ctx),
    )
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    try:
        with opener.open(url, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        return "ERR:%s: %s" % (type(e).__name__, e)


print("=== 1) metadata ===")
r = get(BASE + "/com/android/tools/smali/baksmali/maven-metadata.xml")
if isinstance(r, bytes):
    txt = r.decode("utf-8", "replace")
    vers = re.findall(r"<version>([^<]+)</version>", txt)
    print("  可用版本（末尾 12）:", vers[-12:])
else:
    print(" ", r)
    vers = []

print()
print("=== 2) 逐版本探测 jar 文件名 ===")
candidates = list(reversed(vers[-8:])) if vers else ["3.0.5", "2.5.2"]
plan = None
for v in candidates:
    url = "%s/com/android/tools/smali/baksmali/%s/" % (BASE, v)
    r = get(url)
    if not isinstance(r, bytes):
        print("  %s -> %s" % (v, r))
        continue
    jars = re.findall(r'href="([^"]+\.jar)"', r.decode("utf-8", "replace"))
    jars = [j for j in jars if not j.endswith(".sources.jar") and not j.endswith(".javadoc.jar")]
    print("  %s -> %s" % (v, jars))
    fat = [j for j in jars if "fat" in j]
    pick = fat[0] if fat else (jars[0] if jars else None)
    if pick:
        plan = (v, pick, jars)
        break

if not plan:
    print("\n没找到可用 jar")
    raise SystemExit(1)

v, pick, jars = plan
print()
print("=== 3) 下载（版本 %s，文件 %s）===" % (v, pick))
for name, art in (("baksmali", "baksmali"), ("smali", "smali")):
    # 两边的文件名规则可能不同，先探测 smali 的 jar 列表
    if art != "baksmali":
        url = "%s/com/android/tools/smali/smali/%s/" % (BASE, v)
        rr = get(url)
        if isinstance(rr, bytes):
            sjars = [j for j in re.findall(r'href="([^"]+\.jar)"', rr.decode("utf-8", "replace"))
                     if not j.endswith(".sources.jar") and not j.endswith(".javadoc.jar")]
            sfar = [j for j in sjars if "fat" in j]
            pick2 = sfar[0] if sfar else (sjars[0] if sjars else None)
        else:
            pick2 = None
        if not pick2:
            print("  %s: 没有可用 jar" % art)
            continue
        fname = pick2
    else:
        fname = pick

    url = "%s/com/android/tools/smali/%s/%s/%s" % (BASE, art, v, fname)
    print("  %s <- %s" % (art, fname), flush=True)
    data = get(url, timeout=180)
    if isinstance(data, bytes) and len(data) > 50000:
        dst = os.path.join(OUT, art + ".jar")
        with open(dst, "wb") as f:
            f.write(data)
        print("    OK %d bytes -> %s" % (len(data), dst), flush=True)
    else:
        print("    FAIL %s" % (data if isinstance(data, str) else "too small"), flush=True)

print()
for n in ("baksmali", "smali"):
    p = os.path.join(OUT, n + ".jar")
    if os.path.exists(p):
        print("  %s  %d bytes" % (p, os.path.getsize(p)))
