"""从 LineageOS/android_libcore 全仓搜索「所有文件相关的 native 函数」，产出应 hook 清单。

不靠记忆、不靠文档：直接遍历仓库 git 树，把所有 JNIEXPORT / Java_* / Linux_* 提取出来，
按「文件系统相关」筛选，再与我们的注册表对照。

用法：在 tools/tmp/libcore-src 里能 git ls-tree 即可（shallow clone 可用）。
"""
import io
import re
import subprocess
import os

REPO = "tools/tmp/libcore-src"
MINE = "_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp"

# 与文件/路径/元数据相关的关键字（用于筛选，宁可多收）
FS_KEYWORDS = (
    "open", "openat", "creat", "close", "read", "write", "pread", "pwrite",
    "lseek", "access", "faccessat", "stat", "lstat", "fstat", "fstatat",
    "mkdir", "mkdirat", "rmdir", "unlink", "unlinkat", "rename", "renameat",
    "link", "linkat", "symlink", "symlinkat", "readlink", "readlinkat",
    "chmod", "fchmod", "fchmodat", "chown", "fchown", "fchownat",
    "truncate", "ftruncate", "utimes", "utimensat", "futimes",
    "opendir", "fdopendir", "readdir", "closedir", "dirfd",
    "realpath", "canonicalize", "getcwd", "chdir",
    "statvfs", "fstatvfs", "statfs", "fstatfs",
    "xattr", "getxattr", "setxattr", "listxattr", "removexattr",
    "mkfifo", "mknod", "fdatasync", "fsync", "sync",
    "list", "createDirectory", "createFileExclusively", "delete", "getSpace",
    "getBooleanAttributes", "getLastModifiedTime", "setLastModifiedTime",
    "setPermission", "setReadOnly", "getNameMax", "initIDs", "checkAccess",
    "getLength", "getCanonicalPath", "fsync",
)


def git(*args):
    r = subprocess.run(["git", "-C", REPO] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout


def main():
    files = [f for f in git("ls-tree", "-r", "--name-only", "HEAD").splitlines()
             if f.endswith((".c", ".cpp", ".cc"))]
    native_files = [f for f in files
                    if ("native" in f or f.endswith("_md.c")) and
                    ("libcore" in f or "ojluni" in f or "luni" in f)]
    print(f"仓库内 C/C++ 文件 {len(files)} 个，其中 native 相关 {len(native_files)} 个\n")

    found = {}   # sym -> (file, kind)
    for f in native_files:
        src = git("show", f"HEAD:{f}")
        if not src:
            continue
        # Java_ 形式（标准 JNI 命名）
        for m in re.finditer(r"\bJava_([A-Za-z0-9_]+)\s*\(", src):
            sym = "Java_" + m.group(1)
            found.setdefault(sym, (f, "Java_"))
        # 或者用 JNINativeMethod 表登记（Linux_xxx 形式）
        for m in re.finditer(r'\bLinux_([a-zA-Z0-9_]+)\s*\(', src):
            sym = "Linux_" + m.group(1)
            found.setdefault(sym, (f, "Linux_"))

    # 只保留文件系统相关的
    def is_fs(sym):
        low = sym.lower()
        return any(k.lower() in low for k in FS_KEYWORDS)

    fs_syms = {s: v for s, v in found.items() if is_fs(s)}

    # 我们的注册表
    with io.open(MINE, "r", encoding="utf-8", errors="replace") as fh:
        mine_src = fh.read().replace("\r\n", "\n")
    mine = set(re.findall(r'const_cast<char\*>\("(\w+)"\)', mine_src))

    def our_name(sym):
        """把 AOSP 符号名映射成我们注册的方法名."""
        if sym.startswith("Java_java_io_UnixFileSystem_"):
            return sym[len("Java_java_io_UnixFileSystem_"):]
        if sym.startswith("Linux_"):
            return sym[len("Linux_"):]
        if sym.startswith("Java_sun_nio_fs_UnixNativeDispatcher_"):
            return sym[len("Java_sun_nio_fs_UnixNativeDispatcher_"):]
        return None

    by_group = {"UnixFileSystem": [], "Linux": [], "UnixNativeDispatcher": [], "其他": []}
    for sym, (f, kind) in sorted(fs_syms.items()):
        if sym.startswith("Java_java_io_UnixFileSystem_"):
            g = "UnixFileSystem"
        elif sym.startswith("Linux_"):
            g = "Linux"
        elif sym.startswith("Java_sun_nio_fs_UnixNativeDispatcher_"):
            g = "UnixNativeDispatcher"
        else:
            g = "其他"
        n = our_name(sym)
        covered = n in mine if n else False
        by_group[g].append((sym, f, covered, n))

    for g, items in by_group.items():
        if not items:
            continue
        print("=" * 70)
        print(f"### {g}（{len(items)} 个）")
        cov = [x for x in items if x[2]]
        unc = [x for x in items if not x[2]]
        print(f"  已 hook {len(cov)} 个: {', '.join(x[3] for x in cov) if cov else '（无）'}")
        print(f"  未 hook {len(unc)} 个:")
        for sym, f, _, n in unc:
            print(f"    - {sym:58} {f}")
        print()

    print("=" * 70)
    print("### 我们注册了但 AOSP 里找不到同名符号的（可能是改名/已移除）")
    all_names = {our_name(s) for s in fs_syms if our_name(s)}
    for m in sorted(mine):
        if m not in all_names and m != "nativeLoad":
            print(f"  - {m}")


if __name__ == "__main__":
    main()
