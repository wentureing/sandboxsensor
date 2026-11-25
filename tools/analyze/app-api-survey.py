"""调查多个 app 实际怎么调文件 API —— 判断该 hook 哪几层。

对每个 app：
  1. pull base.apk
  2. 提取所有 dex
  3. 统计每个目标 API 的「字符串池引用数」（是否依赖）
  4. 用 dexdump -d 抽样统计「invoke- 指令数」（是否真的调用）

目标 API 分层：
  L1  java/io/File              （FileSystem 抽象 -> UnixFileSystem.*0）
  L1' java/io/FileInputStream / FileOutputStream / RandomAccessFile
  L2  android/system/Os         （-> BlockGuardOs -> libcore.io.Linux）
  L3  java/nio/file/Files       （NIO2 公开 API -> UnixNativeDispatcher）
  L3' sun/nio/fs/UnixNativeDispatcher（NIO2 私有实现，通常不可直接引用）
  L4  java/nio/channels/FileChannel（走 IoBridge/openat）
"""
import os, re, subprocess, sys, zipfile, tempfile, shutil

APPS = [
    ("mark.via", "轻量浏览器"),
    ("com.deepseek.chat", "AI 客户端"),
    ("io.legado.app.releaseS", "Kotlin 阅读器"),
    ("org.localsend.localsend_app", "跨平台文件传输"),
    ("tv.danmaku.bili", "B 站"),
]

TARGETS = {
    "L1 java.io.File": "Ljava/io/File;",
    "L1' FileOutputStream": "Ljava/io/FileOutputStream;",
    "L1' FileInputStream": "Ljava/io/FileInputStream;",
    "L1' RandomAccessFile": "Ljava/io/RandomAccessFile;",
    "L2 android.system.Os": "Landroid/system/Os;",
    "L3 java.nio.file.Files": "Ljava/nio/file/Files;",
    "L3' UnixNativeDispatcher": "Lsun/nio/fs/UnixNativeDispatcher;",
    "L4 FileChannel": "Ljava/nio/channels/FileChannel;",
    "  (参照) Activity": "Landroid/app/Activity;",
}

def pull(adb, pkg, dst):
    r = subprocess.run([adb, "shell", "pm", "path", pkg],
                       capture_output=True, text=True)
    lines = [l.strip()[len("package:"):] for l in r.stdout.splitlines() if l.startswith("package:")]
    if not lines:
        return None
    base = lines[0]
    with open(dst, "wb") as f:
        rr = subprocess.run([adb, "exec-out", "cat", base], stdout=f, stderr=subprocess.DEVNULL)
    if rr.returncode != 0 or os.path.getsize(dst) < 1024:
        return None
    return base

def extract_dexes(apk):
    out = []
    tmp = tempfile.mkdtemp(prefix="dex-")
    with zipfile.ZipFile(apk) as z:
        for n in z.namelist():
            if n.endswith(".dex"):
                p = os.path.join(tmp, n.replace("/", "_"))
                with open(p, "wb") as f:
                    f.write(z.read(n))
                out.append(p)
    return tmp, out

def count_refs(dex_paths):
    """搜 dex 字节里的类描述符（字符串池是 MUTF-8，直接 find 即可）。"""
    res = {k: 0 for k in TARGETS}
    for p in dex_paths:
        data = open(p, "rb").read()
        for label, desc in TARGETS.items():
            res[label] += data.count(desc.encode("ascii"))
    return res

def count_invokes(dexdump, dex_paths, limit_dex=1):
    """抽样反汇编，统计 invoke-* 到目标类的次数。"""
    res = {k: 0 for k in TARGETS}
    for p in dex_paths[:limit_dex]:
        r = subprocess.run([dexdump, "-d", p], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        for line in r.stdout.splitlines():
            if "invoke-" not in line:
                continue
            for label, desc in TARGETS.items():
                if desc in line:
                    res[label] += 1
    return res

def main():
    bt = sorted(os.path.expanduser("~/AppData/Local/Android/Sdk/build-tools/*/dexdump.exe")
                if os.name == "nt" else [])
    import glob as _g
    cands = sorted(_g.glob(os.path.expanduser(
        r"~/AppData/Local/Android/Sdk/build-tools/*/dexdump.exe")))
    dexdump = cands[-1] if cands else "dexdump"

    tmp_root = tempfile.mkdtemp(prefix="app-survey-")
    rows = []
    for pkg, note in APPS:
        apk = os.path.join(tmp_root, pkg + ".apk")
        base = pull("adb", pkg, apk)
        if base is None:
            print(f"[skip] {pkg} 拉取失败")
            continue
        size_mb = os.path.getsize(apk) / 1048576
        dtmp, dexes = extract_dexes(apk)
        refs = count_refs(dexes)
        inv = count_invokes(dexdump, dexes)
        rows.append((pkg, note, size_mb, len(dexes), refs, inv))
        print(f"[ok] {pkg:32} {size_mb:6.1f}MB dex={len(dexes)}")
        shutil.rmtree(dtmp, ignore_errors=True)
        os.remove(apk)

    print("\n=== 字符串池引用数（是否依赖该 API）===")
    header = f"{'app':32}" + "".join(f"{k.split()[0]:>10}" for k in TARGETS)
    print(header)
    for pkg, note, sz, nd, refs, inv in rows:
        line = f"{pkg:32}" + "".join(f"{refs[k]:>10}" for k in TARGETS)
        print(line)

    print("\n=== invoke 指令数（是否真的调用；只抽样首个 dex）===")
    print(header)
    for pkg, note, sz, nd, refs, inv in rows:
        line = f"{pkg:32}" + "".join(f"{inv[k]:>10}" for k in TARGETS)
        print(line)

    print("\n=== 逐 app 结论 ===")
    for pkg, note, sz, nd, refs, inv in rows:
        used = [k for k in TARGETS if inv[k] > 0 and not k.startswith("  ")]
        print(f"  {pkg:32} 实际调用: {', '.join(used) if used else '（无，纯 Native 或未抽样到）'}")

    shutil.rmtree(tmp_root, ignore_errors=True)

if __name__ == "__main__":
    main()
