"""验证「为什么 File 的 native 在 UnixFileSystem」—— 看类结构与委托关系。

关键假设（JDK 的 FileSystem 抽象）：
  java.io.File           有静态字段 fs : FileSystem
  java.io.FileSystem     是抽象类，声明 list/createDirectory/delete/rename 等
  java.io.UnixFileSystem 继承 FileSystem，把 *0 方法声明为 native
  java.io.File 的方法内部通过 fs.xxx() 委托

如果成立，那么：
  * 「在哪个文件」不是随意的，是 JDK 的抽象层决定
  * 换 Android 版本只会换实现类/方法名，抽象契约不变
  * 换 app 完全不影响（app 只碰 File / Os 这两层公开 API）
"""
import subprocess, re, os, glob

bt = sorted(glob.glob(os.path.expanduser(
    r"~/AppData/Local/Android/Sdk/build-tools/*/dexdump.exe")))
dexdump = bt[-1]
OJ = "dist/libcore/oj.dex"

# 快速模式（不加 -d），只解析类结构
out = subprocess.run([dexdump, OJ], capture_output=True, text=True,
                     encoding="utf-8", errors="replace")
text = out.stdout
print("dexdump(结构模式) 输出长度:", len(text))

parts = re.split(r"Class descriptor\s*:\s*'([^']+)'", text)
classes = {}
for i in range(1, len(parts) - 1, 2):
    classes[parts[i]] = parts[i + 1]

def show(desc, label):
    body = classes.get(desc)
    if body is None:
        print(f"  {label}: 不在这个 dex")
        return None
    m = re.search(r"Superclass\s*:\s*'([^']*)'", body)
    sup = m.group(1) if m else "?"
    access = []
    if re.search(r"Access flags\s*:\s*0x[0-9a-f]+\s*\(([^)]*)\)", body):
        access = re.search(r"Access flags\s*:\s*0x[0-9a-f]+\s*\(([^)]*)\)", body).group(1).split()
    print(f"  {label:24} super={sup:28} flags={','.join(access)}")
    return body

print("\n=== 类继承关系 ===")
file_body   = show("Ljava/io/File;", "java.io.File")
fs_body     = show("Ljava/io/FileSystem;", "java.io.FileSystem")
ufs_body    = show("Ljava/io/UnixFileSystem;", "java.io.UnixFileSystem")

print("\n=== java.io.File 里的 FileSystem 字段 ===")
if file_body:
    for m in re.finditer(r"name\s*:\s*'([^']+)'\s*\n\s*type\s*:\s*'([^']+)'", file_body):
        n, t = m.group(1), m.group(2)
        if "FileSystem" in t or n in ("fs", "path", "prefixLength"):
            print(f"  {n:18} {t}")

print("\n=== java.io.FileSystem 声明的方法（抽象契约）===")
if fs_body:
    for m in re.finditer(r"name\s*:\s*'([^']+)'\s*\n\s*type\s*:\s*'([^']+)'\s*\n\s*access\s*:\s*([^\n]*)", fs_body):
        n, t, a = m.group(1), m.group(2), m.group(3)
        nat = "NATIVE" in a
        abst = "ABSTRACT" in a
        if any(k in n.lower() for k in ("list", "create", "delete", "rename", "mkdir")):
            tag = "NATIVE" if nat else ("ABSTRACT" if abst else "java")
            print(f"  {tag:9} {n:24} {t}")

print("\n=== java.io.UnixFileSystem 的 *0 native 方法（实现）===")
if ufs_body:
    for m in re.finditer(r"name\s*:\s*'([^']+)'\s*\n\s*type\s*:\s*'([^']+)'\s*\n\s*access\s*:\s*([^\n]*)", ufs_body):
        n, t, a = m.group(1), m.group(2), m.group(3)
        if "NATIVE" in a:
            print(f"  NATIVE    {n:24} {t}")

print("\n=== 交叉：libcore.io.Linux 是否被 File 使用（看 File 的方法体不在结构模式里）===")
print("  （方法体需要 -d 模式，这里只确认结构：UnixFileSystem 的 *0 是 native，")
print("    而 Linux 类里没有 opendir，所以 File.listFiles 不可能走 Linux）")
