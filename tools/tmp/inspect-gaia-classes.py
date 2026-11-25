"""看 Gaia 的两个关键类：
  com/prism/gaia/helper/io/GFile        <- 名字就是 "Gaia File"，可能是 File 的包装
  com/prism/gaia/client/natives/NativeMirror  <- so 里出现过，JNI 桥

用 dexdump 的结构模式（不加 -d，快）。
"""
import glob
import os
import re
import subprocess
import zipfile

z = zipfile.ZipFile("dist/gaia.apk")
tmp = "dist/gaia-dex"
os.makedirs(tmp, exist_ok=True)
for n in z.namelist():
    if n.endswith(".dex"):
        p = os.path.join(tmp, os.path.basename(n))
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(z.read(n))

bt = sorted(glob.glob(os.path.expanduser(
    r"~/AppData/Local/Android/Sdk/build-tools/*/dexdump.exe")))
dexdump = bt[-1]

WANT = ["Lcom/prism/gaia/helper/io/GFile;",
        "Lcom/prism/gaia/client/natives/NativeMirror;"]

for dex in sorted(os.listdir(tmp)):
    path = os.path.join(tmp, dex)
    r = subprocess.run([dexdump, path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    txt = r.stdout
    for want in WANT:
        pat = "Class descriptor\\s*:\\s*'" + re.escape(want).replace("/", "/") + "'([\\s\\S]*?)(?=Class descriptor\\s*:|\\Z)"
        m = re.search(pat, txt)
        if not m:
            continue
        body = m.group(1)
        print("=" * 70)
        print("=== %s  (in %s) ===" % (want, dex))
        sup = re.search(r"Superclass\s*:\s*'([^']*)'", body)
        if sup:
            print("  super: %s" % sup.group(1))
        print("  --- 方法 ---")
        for mm in re.finditer(
                r"name\s*:\s*'([^']+)'\s*\n\s*type\s*:\s*'([^']+)'\s*\n\s*access\s*:\s*([^\n]*)",
                body):
            name, sig, acc = mm.group(1), mm.group(2), mm.group(3)
            tag = "NATIVE" if "NATIVE" in acc else ("STATIC" if "STATIC" in acc else "java")
            print("    [%-6s] %-32s %s" % (tag, name, sig))
        print()
