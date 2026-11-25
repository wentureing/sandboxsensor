import glob
import os
import re
import subprocess
import tempfile
import zipfile

BT = sorted(glob.glob(os.path.expanduser(
    r"~/AppData/Local/Android/Sdk/build-tools/*/dexdump.exe")))
dexdump = BT[-1]
apk = (glob.glob("dist/via.apk") + glob.glob("**/via.apk", recursive=True))[0]

tmp = tempfile.mkdtemp()
z = zipfile.ZipFile(apk)
dexes = []
for n in z.namelist():
    if n.endswith(".dex"):
        p = os.path.join(tmp, os.path.basename(n))
        open(p, "wb").write(z.read(n))
        dexes.append(p)

print("dex:", [os.path.basename(d) for d in dexes])
print()

for d in dexes:
    r = subprocess.run([dexdump, "-d", d], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    text = r.stdout
    # 按方法切分
    parts = re.split(r"Class descriptor\s*:\s*'([^']+)'", text)
    for i in range(1, len(parts) - 1, 2):
        cls = parts[i]
        body = parts[i + 1]
        if "loadUrl" not in body and "shouldInterceptRequest" not in body:
            continue
        # 再按方法切
        mparts = re.split(r"\n\s*name\s*:\s*'([^']+)'\s*\n\s*type\s*:\s*'([^']+)'", body)
        for j in range(1, len(mparts) - 2, 3):
            mname, msig = mparts[j], mparts[j + 1]
            mbody = mparts[j + 2]
            for kw in ("loadUrl", "shouldInterceptRequest"):
                if ("WebView." + kw) in mbody or ("." + kw + ":") in mbody:
                    print("=" * 70)
                    print(f"{cls}  ->  {mname}{msig}   [含 {kw}]")
                    # 打印含 loadUrl / shouldInterceptRequest 的指令及其前 6 行
                    lines = mbody.split("\n")
                    for k, line in enumerate(lines):
                        if kw + ":" in line:
                            lo = max(0, k - 7)
                            for x in lines[lo:k + 2]:
                                print("   ", x.strip()[:150])
                            print("    ---")
