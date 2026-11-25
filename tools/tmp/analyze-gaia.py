"""解包 Gaia（应用隐藏大师），找它怎么做路径镜像。

关注点：
  1. 有没有 native 库（做 GOT patch / mount / namespace）
  2. dex 里有没有 "gaia/" 路径模板字符串
  3. getExternalFilesDir / getDataDir 的实现痕迹
  4. 是否加壳（dex 加密）
"""
import os
import re
import zipfile

APK = "dist/gaia.apk"

if not os.path.exists(APK):
    raise SystemExit("先导出 dist/gaia.apk")

size_mb = os.path.getsize(APK) / 1048576
print("=== APK: %s  (%.1f MB) ===" % (APK, size_mb))

z = zipfile.ZipFile(APK)
names = z.namelist()

dexes = [n for n in names if n.endswith(".dex")]
sos = [n for n in names if n.endswith(".so")]
assets = [n for n in names if n.startswith("assets/")]

print("\n=== 结构 ===")
print("  dex: %d 个  %s" % (len(dexes), dexes[:10]))
print("  so : %d 个" % len(sos))
for s in sos[:20]:
    print("       %-60s %6d KB" % (s, z.getinfo(s).file_size // 1024))
print("  assets 前 10:")
for a in assets[:10]:
    print("       %-60s %6d KB" % (a, z.getinfo(a).file_size // 1024))

# ---------- dex 字符串搜索 ----------
print("\n=== dex 里的关键字符串 ===")
PATTERNS = [
    b"gaia/",
    b"/gaia",
    b"getExternalFilesDir",
    b"getDataDir",
    b"synthetic",
    b"sdcard/Android/data",
    b"/data/user/0/",
    b"nativeBridge",           # 有些容器用
    b"Riru", b"Zygisk", b"Xposed",
    b"mount", b"unshare", b"pivot_root",
    b"ptrace",
    b"isolated",
]
for n in dexes:
    data = z.read(n)
    print("\n  --- %s (%d KB) ---" % (n, len(data) // 1024))
    for p in PATTERNS:
        c = data.count(p)
        if c:
            print("    %-24s %d 次" % (p.decode("ascii", "replace"), c))

# ---------- so 里的关键符号 ----------
print("\n=== .so 里的关键符号（找路径/挂载相关）===")
KEY = [b"mount", b"unshare", b"pivot_root", b"path", b"gaia", b"redirect",
       b"stat", b"openat"]
for s in sos:
    if "arm64" not in s:
        continue
    data = z.read(s)
    hits = []
    for k in KEY:
        c = data.count(k)
        if c:
            hits.append("%s=%d" % (k.decode("ascii", "replace"), c))
    if hits:
        print("  %-55s %s" % (s, " ".join(hits[:6])))

# ---------- 判断是否加壳 ----------
print("\n=== 是否加壳（dex 头部检查）===")
for n in dexes:
    head = z.read(n)[:8]
    ok = head[:4] == b"dex\n"
    print("  %-14s magic=%r  %s" % (n, head, "正常 dex" if ok else "**非标准 -> 可能加壳/加密**"))
