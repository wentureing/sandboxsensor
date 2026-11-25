"""分析 Gaia 的 libgaianative_64.so：它到底做了什么。

想知道：
  1. 有没有 hook 相关（GOT patch / inline hook / trampoline）
  2. 有没有 mount / unshare / namespace
  3. 有没有路径模板（"gaia/" 之类）
  4. 导出了哪些 JNI 函数（Java 层调它做什么）
"""
import io
import re
import struct
import zipfile

APK = "dist/gaia.apk"
z = zipfile.ZipFile(APK)


def read_elf(name):
    return z.read(name)


def parse_dynsym(data, is64=True):
    e_shoff = struct.unpack_from("<Q", data, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", data, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", data, 0x3C)[0]
    e_shstrndx = struct.unpack_from("<H", data, 0x3E)[0]

    def sh(i):
        off = e_shoff + i * e_shentsize
        vals = struct.unpack_from("<IIQQQQIIQQ", data, off)
        return dict(name=vals[0], typ=vals[1], offset=vals[4], size=vals[5],
                    link=vals[6], entsize=vals[9])

    secs = [sh(i) for i in range(e_shnum)]
    shstr = data[secs[e_shstrndx]["offset"]:secs[e_shstrndx]["offset"] + secs[e_shstrndx]["size"]]

    def nm(n):
        e = shstr.find(b"\0", n)
        return shstr[n:e].decode("utf-8", "replace")

    dynsym = dynstr = None
    for s in secs:
        n = nm(s["name"])
        if n == ".dynsym":
            dynsym = s
        elif n == ".dynstr":
            dynstr = s
    if not dynsym:
        return []

    strtab = data[dynstr["offset"]:dynstr["offset"] + dynstr["size"]]
    out = []
    cnt = dynsym["size"] // dynsym["entsize"]
    for i in range(cnt):
        off = dynsym["offset"] + i * dynsym["entsize"]
        st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from("<IBBHQQ", data, off)
        e = strtab.find(b"\0", st_name)
        s = strtab[st_name:e].decode("utf-8", "replace")
        bind = st_info >> 4
        if s and bind in (1, 2) and st_shndx != 0:   # GLOBAL / WEAK 且已定义
            out.append(s)
    return out


for so in ["lib/arm64-v8a/libgaianative_64.so", "lib/arm64-v8a/libgaianative.so",
           "lib/arm64-v8a/libhelper.so"]:
    try:
        data = read_elf(so)
    except KeyError:
        continue
    print("=" * 70)
    print("=== %s  (%.0f KB) ===" % (so, len(data) / 1024))

    syms = parse_dynsym(data)
    print("  导出符号 %d 个" % len(syms))

    # JNI 入口
    jni = [s for s in syms if s.startswith("Java_")]
    print("\n  --- JNI 入口 (%d) ---" % len(jni))
    for s in jni[:25]:
        print("    ", s)

    # 可疑：hook / mount / 路径
    SUSPECT = ("hook", "Hook", "patch", "Patch", "inline", "trampoline",
               "mount", "unmount", "unshare", "pivot", "namespace",
               "redirect", "Redirect", "translate", "Translate",
               "path", "Path", "stat", "openat", "getdents")
    hits = [s for s in syms if any(k in s for k in SUSPECT)]
    print("\n  --- 与 hook/mount/路径相关的导出符号 (%d) ---" % len(hits))
    for s in hits[:30]:
        print("    ", s)

    # 字符串里的路径模板
    print("\n  --- .rodata 里的路径模板 ---")
    for pat in [b"gaia/", b"/gaia", b"/data/user/0/", b"sdcard/Android/data",
                b"/proc/self/", b"mount", b"magisk", b"riru", b"zygisk"]:
        c = data.count(pat)
        if c:
            print("    %-24s %d 次" % (pat.decode(), c))

    # 附近上下文（找含 gaia 的可读串）
    print("\n  --- 含 'gaia' 的可读字符串（前 12 条）---")
    found = set()
    for m in re.finditer(rb"[ -~]{6,}", data):
        s = m.group(0)
        if b"gaia" in s.lower():
            t = s.decode("ascii", "replace")
            if t not in found:
                found.add(t)
                print("    ", t[:100])
                if len(found) >= 12:
                    break
    print()
