"""系统化筛出：还需要参考 AOSP 的点位。

方法：
  1. 从 UnixFileSystem_md.c 提取所有 JNIEXPORT 函数（Java 层能用到的 native）
  2. 从 libcore_io_Linux.cpp 提取文件相关的 Linux_* 函数
  3. 从 native-hook.cpp 提取我们已注册的方法名
  4. 三方对照 -> 输出「已覆盖 / 未覆盖 / 需核对」三类清单
"""
import re
import io

UFS = "tools/ref/UnixFileSystem_md.c"
LINUX = "tools/ref/libcore_io_Linux.cpp"
MINE = "_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp"


def read(p):
    with io.open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n")


# ---------- 1) AOSP: UnixFileSystem 的 JNI 函数 ----------
ufs_src = read(UFS)
ufs_fns = re.findall(r"Java_java_io_UnixFileSystem_(\w+)\s*\(", ufs_src)
ufs_fns = sorted(set(ufs_fns))
print(f"=== AOSP UnixFileSystem_md.c：{len(ufs_fns)} 个 JNI 函数 ===")
for f in ufs_fns:
    print(f"  {f}")

# ---------- 2) AOSP: Linux 里与文件相关的 ----------
linux_src = read(LINUX)
linux_fns = re.findall(r"static \w[\w\s\*]*?\bLinux_(\w+)\s*\(", linux_src)
linux_fns = sorted(set(linux_fns))
FILE_RELATED = [
    "open", "openat", "close", "access", "mkdir", "rmdir", "unlink", "unlinkat",
    "rename", "renameat", "stat", "lstat", "fstat", "fstatat", "readlink",
    "chmod", "fchmod", "chown", "fchown", "truncate", "ftruncate",
    "getxattr", "setxattr", "listxattr", "removexattr",
    "opendir", "fdopendir", "readdir",
    "link", "symlink", "mkfifo", "fdatasync", "fsync", "fsync",
    "realpath", "statvfs", "fstatvfs",
]
linux_file = [f for f in linux_fns if f in FILE_RELATED]
print(f"\n=== AOSP libcore_io_Linux.cpp：{len(linux_fns)} 个 Linux_* 函数，其中文件相关 {len(linux_file)} 个 ===")
for f in linux_file:
    print(f"  {f}")

# ---------- 3) 我们的注册表 ----------
mine_src = read(MINE)
regs = re.findall(r'const_cast<char\*>\("(\w+)"\),\s*\n\s*const_cast<char\*>\("([^"]+)"\)', mine_src)
mine_names = sorted({n for n, _ in regs})
print(f"\n=== 我们已注册 {len(regs)} 个方法（去重后 {len(mine_names)} 个名字）===")
for n in mine_names:
    print(f"  {n}")

# ---------- 4) 对照 ----------
covered_ufs = [f for f in ufs_fns if f in mine_names]
miss_ufs = [f for f in ufs_fns if f not in mine_names]
covered_linux = [f for f in linux_file if f in mine_names]
miss_linux = [f for f in linux_file if f not in mine_names]

print("\n" + "=" * 60)
print("=== UnixFileSystem：已覆盖 / 未覆盖 ===")
print(f"  已覆盖（{len(covered_ufs)}）: {', '.join(covered_ufs)}")
print(f"  未覆盖（{len(miss_ufs)}）:")
for f in miss_ufs:
    print(f"    - {f}")

print("\n=== Linux（文件相关）：已覆盖 / 未覆盖 ===")
print(f"  已覆盖（{len(covered_linux)}）: {', '.join(covered_linux)}")
print(f"  未覆盖（{len(miss_linux)}）:")
for f in miss_linux:
    print(f"    - {f}")

# ---------- 5) 按「File/Os 是否真的会调用」分级 ----------
print("\n" + "=" * 60)
print("=== 优先级建议（按 File/Os 的实际调用路径）===")
PRIORITY = {
    # UnixFileSystem 侧
    "getLastModifiedTime0": ("P0", "File.lastModified()"),
    "getSpace0":            ("P1", "File.getUsableSpace()/getTotalSpace()"),
    "canonicalize0":        ("P1", "File.getCanonicalPath()"),
    "setPermission0":       ("P2", "File.setExecutable/setReadable/setWritable"),
    "setReadOnly0":         ("P2", "File.setReadOnly()"),
    "setLastModifiedTime0": ("P2", "File.setLastModified()"),
    "getNameMax0":          ("skip", "常量：文件名长度上限"),
    "initIDs":              ("skip", "native 初始化，不能替换"),
    "delete0":              ("skip", "Android 已改用 Os.remove（我们已 hook Linux.unlink）"),
    "rename0":              ("skip", "Android 已改用 Os.rename（我们已 hook Linux.rename）"),
    # Linux 侧
    "fstat":                ("P0", "FileInputStream 构造时 fstat（本次日志已暴露 EBADF）"),
    "stat":                 ("P0", "File.length() 走 Os.stat"),
    "lstat":                ("P1", "Os.lstat（少数场景）"),
    "readlink":             ("P1", "File.getCanonicalPath 部分路径 / Os.readlink"),
    "chmod":                ("P2", "File.setReadable 等（部分走 UnixFileSystem.setPermission0）"),
    "fchmod":               ("P2", "同上"),
    "truncate":             ("P2", "RandomAccessFile.setLength 可能用"),
    "ftruncate":            ("P2", "同上"),
    "opendir":              ("skip", "libcore.io.Linux 里没有这个方法"),
    "close":                ("P2", "一般无需翻译（无路径参数）"),
    "getxattr":             ("P3", "少数 app 读文件属性"),
    "listxattr":            ("P3", "同上"),
}
for grp, items in (("UnixFileSystem", miss_ufs), ("Linux", miss_linux)):
    print(f"\n  --- {grp} ---")
    for f in items:
        pr, why = PRIORITY.get(f, ("?", "未评估"))
        print(f"    [{pr:4}] {f:24} {why}")

print("\n" + "=" * 60)
print("=== 已覆盖但还需逐项核对 AOSP 行为的（不能只看名字）===")
NEED_BEHAVIOR_CHECK = [
    ("open", "createFileDescriptorIfOpen 的行为；错误路径是否 return NULL + 挂起异常"),
    ("openat", "dirfd 相对路径的翻译（我们现在只翻译绝对路径）"),
    ("access", "失败抛 ErrnoException + return (rc==0)；已核对"),
    ("mkdir", "throwIfMinusOne；mode 由调用方传（File 用 0777）"),
    ("unlink", "throwIfMinusOne；无 EINTR 包裹（AOSP 原样）"),
    ("rename", "TEMP_FAILURE_RETRY；两个参数都检查 null"),
    ("list0", "扩容策略 16->翻倍；str_class 空检查；错误路径 closedir"),
    ("createDirectory0", "mkdir(path, 0777)；失败 return false"),
    ("createFileExclusively0", "已按 AOSP 三处修正"),
    ("getBooleanAttributes0", "statMode 用 stat64；不含 BA_HIDDEN；失败 return 0"),
]
for n, note in NEED_BEHAVIOR_CHECK:
    print(f"  {n:24} {note}")
