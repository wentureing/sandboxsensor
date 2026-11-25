import glob
import os
import re
import subprocess
import tempfile
import zipfile

BT = sorted(glob.glob(os.path.expanduser(
    r"~/AppData/Local/Android/Sdk/build-tools/*/dexdump.exe")))
dexdump = BT[-1]
apk = "dist/wv/wv.apk"

tmp = tempfile.mkdtemp()
z = zipfile.ZipFile(apk)
dexes = []
for n in z.namelist():
    if n.endswith(".dex"):
        p = os.path.join(tmp, n.replace("/", "_"))
        with open(p, "wb") as f:
            f.write(z.read(n))
        dexes.append(p)

print("dex 数量:", len(dexes))
print()

WANT_CLASSES = [
    "Lorg/chromium/android_webview/AwContents;",
    "Landroid/webkit/WebView;",
]

for d in dexes:
    r = subprocess.run([dexdump, d], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    text = r.stdout
    parts = re.split(r"Class descriptor\s*:\s*'([^']+)'", text)
    for i in range(1, len(parts) - 1, 2):
        cls = parts[i]
        if cls not in WANT_CLASSES:
            continue
        body = parts[i + 1]
        print("=" * 72)
        print(f"{cls}   ({os.path.basename(d)})")
        natives = []
        for m in re.finditer(
                r"name\s*:\s*'([^']+)'\s*\n\s*type\s*:\s*'([^']+)'\s*\n\s*access\s*:\s*([^\n]*)",
                body):
            name, sig, acc = m.group(1), m.group(2), m.group(3)
            if "NATIVE" in acc and ("oad" in name or "Url" in name or "url" in name):
                natives.append((name, sig))
        if natives:
            print("  NATIVE 且名字含 load/Url:")
            for n, s in natives:
                print(f"    {n:40} {s}")
        else:
            print("  （没有名字含 load/Url 的 native 方法）")
        # 也打印所有 native 数量
        alln = re.findall(r"access\s*:\s*[^\n]*NATIVE", body)
        print(f"  native 方法总数: {len(alln)}")
        print()
