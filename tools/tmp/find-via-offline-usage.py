"""在 via 的 dex 里找 "offline" 的用法：它出现在哪个类/方法里。"""
import os
import re
import subprocess
import glob
import zipfile

z = zipfile.ZipFile("dist/via.apk")
tmp = "dist/via-dex"
os.makedirs(tmp, exist_ok=True)
dex_paths = []
for n in z.namelist():
    if n.endswith(".dex"):
        p = os.path.join(tmp, os.path.basename(n))
        if not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(z.read(n))
        dex_paths.append(p)

bt = sorted(glob.glob(os.path.expanduser(
    r"~/AppData/Local/Android/Sdk/build-tools/*/dexdump.exe")))
dexdump = bt[-1]

for dex in dex_paths:
    r = subprocess.run([dexdump, "-d", dex], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    text = r.stdout
    print("=== %s  dexdump %d KB ===" % (os.path.basename(dex), len(text) // 1024))

    # 按类切分
    parts = re.split(r"Class descriptor\s*:\s*'([^']+)'", text)
    hits = []
    for i in range(1, len(parts) - 1, 2):
        desc = parts[i]
        body = parts[i + 1]
        if "offline" not in body:
            continue
        # 找该 body 里所有含 offline 的行
        lines = [l.strip() for l in body.splitlines() if "offline" in l]
        # 找该 body 里的方法名
        meths = re.findall(r"name\s*:\s*'([^']+)'\s*\n\s*type\s*:\s*'([^']+)'", body)
        hits.append((desc, lines[:6], [m[0] for m in meths][:8]))

    print("  含 offline 的类: %d" % len(hits))
    for desc, lines, meths in hits[:10]:
        print("\n  --- %s ---" % desc)
        print("      方法:", meths)
        for l in lines:
            print("      |", l[:130])

    # 顺便看 ListFiles/FileFilter 用法
    print("\n  --- 与 listFiles 相关的字符串 ---")
    for pat in ["listFiles", "FileFilter", "FilenameFilter", "newDirectoryStream",
                "Files.list", "File.list"]:
        c = text.count(pat)
        if c:
            print("    %-22s %d 次" % (pat, c))
    print()
