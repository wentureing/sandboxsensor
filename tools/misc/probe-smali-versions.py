"""探测 smali/baksmali 的正确版本与下载路径。"""
import ssl
import urllib.request
import re

PROXY = "http://127.0.0.1:7897"
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def get(url, timeout=60):
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


print("=== 1) maven-metadata（baksmali 有哪些版本）===")
r = get("https://repo1.maven.org/maven2/com/android/tools/smali/baksmali/maven-metadata.xml")
if isinstance(r, bytes):
    txt = r.decode("utf-8", "replace")
    vers = re.findall(r"<version>([^<]+)</version>", txt)
    print("  版本:", vers[-10:])
else:
    print(" ", r)

print()
print("=== 2) 探测某个版本的目录内容 ===")
for v in ["3.0.5", "3.0.9", "2.5.2"]:
    url = "https://repo1.maven.org/maven2/com/android/tools/smali/baksmali/%s/" % v
    r = get(url)
    if isinstance(r, bytes):
        files = re.findall(r'href="([^"]+\.jar)"', r.decode("utf-8", "replace"))
        print("  %s -> %s" % (v, files[:6] if files else "(无 jar)"))
    else:
        print("  %s -> %s" % (v, r))

print()
print("=== 3) GitHub releases 探测 ===")
for v in ["v3.0.5", "v3.0.9"]:
    url = "https://github.com/baksmali/smali/releases/download/%s/baksmali-%s-fat.jar" % (v, v.lstrip("v"))
    r = get(url, timeout=90)
    print("  %s -> %s" % (url.split("/")[-1], ("OK %d bytes" % len(r)) if isinstance(r, bytes) else r))
