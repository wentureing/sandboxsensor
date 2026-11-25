"""检查 libjavacore.so 是否导出 Java_* JNI 符号（决定能否用 dlsym 包装原实现）。

如果导出 -> 可以写"包装式 hook"：
    orig = dlsym(RTLD_DEFAULT, "Java_java_io_UnixFileSystem_list0");
    我们的 hook 里：翻译路径 -> 构造新参数 -> 调用 orig -> 原样返回
  => 不需要复刻 AOSP 的任何行为（异常/返回值都由原实现决定）

如果不导出 -> 只能用 RegisterNatives 替换 + 手工仿真。
"""
import struct
import sys

PATH = "dist/native/libjavacore.so"
WANT = [
    "Java_java_io_UnixFileSystem_list0",
    "Java_java_io_UnixFileSystem_createDirectory0",
    "Java_java_io_UnixFileSystem_createFileExclusively0",
    "Java_java_io_UnixFileSystem_getBooleanAttributes0",
    "Java_java_io_UnixFileSystem_getLastModifiedTime0",
    "Java_libcore_io_Linux_open",
    "Java_libcore_io_Linux_access",
    "Java_libcore_io_Linux_mkdir",
    "Java_libcore_io_Linux_stat",
    "Java_libcore_io_Linux_fstat",
]

with open(PATH, "rb") as f:
    data = f.read()

print(f"文件: {PATH}  ({len(data)} bytes)")
if data[:4] != b"\x7fELF":
    sys.exit("不是 ELF")
is64 = data[4] == 2
print(f"  ELF{'64' if is64 else '32'}")

# 解析 section headers，找 .dynstr / .dynsym
e_shoff = struct.unpack_from("<Q", data, 0x28)[0] if is64 else struct.unpack_from("<I", data, 0x20)[0]
e_shentsize = struct.unpack_from("<H", data, 0x3A)[0]
e_shnum = struct.unpack_from("<H", data, 0x3C)[0]
e_shstrndx = struct.unpack_from("<H", data, 0x3E)[0]

def sh(i):
    off = e_shoff + i * e_shentsize
    if is64:
        name, typ, flags, addr, offset, size, link, info, align, entsize = struct.unpack_from("<IIQQQQIIQQ", data, off)
    else:
        name, typ, flags, addr, offset, size, link, info, align, entsize = struct.unpack_from("<IIIIIIIIII", data, off)
    return dict(name=name, typ=typ, offset=offset, size=size, link=link, entsize=entsize)

sections = [sh(i) for i in range(e_shnum)]
shstr = sections[e_shstrndx]
shstrtab = data[shstr["offset"]:shstr["offset"] + shstr["size"]]

def shname(n):
    end = shstrtab.find(b"\0", n)
    return shstrtab[n:end].decode("utf-8", "replace")

dynsym = dynstr = None
for s in sections:
    nm = shname(s["name"])
    if nm == ".dynsym":
        dynsym = s
    elif nm == ".dynstr":
        dynstr = s

if dynsym is None or dynstr is None:
    sys.exit("没有 .dynsym/.dynstr")

strtab = data[dynstr["offset"]:dynstr["offset"] + dynstr["size"]]
count = dynsym["size"] // dynsym["entsize"]
print(f"  .dynsym: {count} 个符号")

all_syms = []
for i in range(count):
    off = dynsym["offset"] + i * dynsym["entsize"]
    if is64:
        st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from("<IBBHQQ", data, off)
    else:
        st_name, st_value, st_size, st_info, st_other, st_shndx = struct.unpack_from("<IIIBBH", data, off)
    end = strtab.find(b"\0", st_name)
    nm = strtab[st_name:end].decode("utf-8", "replace")
    bind = st_info >> 4
    vis = st_other & 0x03
    all_syms.append((nm, bind, vis, st_shndx))

print("\n=== 目标符号是否导出（bind=1 GLOBAL, vis=0 DEFAULT）===")
found_any = False
for w in WANT:
    hit = [s for s in all_syms if s[0] == w]
    if hit:
        nm, bind, vis, shndx = hit[0]
        ok = (bind == 1) and (vis == 0) and (shndx != 0)
        tag = "EXPORTED" if ok else "local/hidden"
        print(f"  [{tag:12}] {w}  bind={bind} vis={vis} shndx={shndx}")
        if ok:
            found_any = True
    else:
        print(f"  [NOT FOUND   ] {w}")

print("\n=== 顺带：所有 Java_* 符号（前 20 个）===")
java_syms = [s[0] for s in all_syms if s[0].startswith("Java_")]
print(f"  共 {len(java_syms)} 个")
for s in java_syms[:20]:
    print(f"    {s}")

print()
if found_any:
    print("结论：**至少部分 Java_* 是导出的** -> 可以用 dlsym 拿原实现做「包装式 hook」")
else:
    print("结论：Java_* 未导出 -> 只能用 RegisterNatives 替换 + 手工仿真")
