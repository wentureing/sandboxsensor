"""从「调用方假设」反推 hook 必须满足的行为契约。

方法：对每个 hook 的 native 方法，列出**所有调用方**，看它们对返回值/异常做了什么假设。
这些假设就是契约；违反任何一条都会在某个 app 的某个路径上崩。

本脚本不执行，只作为清单生成的依据（人工整理进文档）。
"""

# 结构化整理：被 hook 的方法 -> 已知调用方 -> 调用方假设
CONTRACTS = [
    {
        "api": "Linux.open(String,int,int) -> FileDescriptor",
        "callers": [
            ("IoUtils.canOpenReadOnly", "fd = os.open(...); finally { os.close(fd); }",
             "**假定返回值永远非 null**；失败必须抛 ErrnoException"),
            ("IoBridge.open", "return os.open(path, flags, mode);",
             "返回值直接透传，失败必须抛 ErrnoException"),
            ("BlockGuardOs.open", "审计后委托 Linux.open",
             "同上"),
        ],
        "violation": "返回 null -> close(null) -> BlockGuardOs.close -> fd.isSocket$() -> NPE -> FATAL",
        "fix": "path==null 抛 NPE；其余失败抛 ErrnoException；绝不返回 null",
    },
    {
        "api": "Linux.access(String,int) -> boolean",
        "callers": [
            ("UnixFileSystem.checkAccess", "try { os.access(...); return true; } catch (ErrnoException e) { return false; }",
             "**假定失败抛 ErrnoException**，靠 catch 得到 false"),
            ("File.canRead/canWrite/canExecute", "间接走 checkAccess",
             "同上"),
        ],
        "violation": "返回 false -> catch 不到 -> 语义变成'永远不可读'；抛别的异常类型 -> 逃逸",
        "fix": "失败抛 ErrnoException；path==null 抛 NPE",
    },
    {
        "api": "Linux.mkdir(String,int) -> void",
        "callers": [
            ("Libcore.os.mkdir", "直接透传",
             "失败抛 ErrnoException"),
        ],
        "violation": "失败不抛 -> 调用方以为成功",
        "fix": "失败抛 ErrnoException；EINTR 重试",
    },
    {
        "api": "Linux.unlink(String) -> void",
        "callers": [
            ("UnixFileSystem.delete", "try { os.remove(path); return true; } catch (ErrnoException e) { return false; }",
             "失败抛 ErrnoException，由调用方转成 false"),
        ],
        "violation": "失败不抛 -> File.delete 返回 true 但没删掉",
        "fix": "失败抛 ErrnoException",
    },
    {
        "api": "Linux.rename(String,String) -> void",
        "callers": [
            ("UnixFileSystem.rename", "try { os.rename(...); return true; } catch (ErrnoException e) { return false; }",
             "失败抛 ErrnoException"),
        ],
        "violation": "同上",
        "fix": "失败抛 ErrnoException",
    },
    {
        "api": "UnixFileSystem.list0(File) -> String[]",
        "callers": [
            ("UnixFileSystem.list", "String[] r = list0(f); return r;",
             "**失败返回 null**（不是抛异常）"),
        ],
        "violation": "失败抛异常 -> File.listFiles 抛未声明异常",
        "fix": "失败返回 null；不抛异常",
    },
    {
        "api": "UnixFileSystem.createDirectory0(File) -> boolean",
        "callers": [("UnixFileSystem.createDirectory", "return createDirectory0(f);", "失败返回 false")],
        "violation": "抛异常 -> File.mkdirs 抛未声明异常",
        "fix": "失败返回 false",
    },
    {
        "api": "UnixFileSystem.createFileExclusively0(String) -> boolean",
        "callers": [("UnixFileSystem.createFileExclusively", "return ...0(path);", "失败返回 false")],
        "violation": "同上",
        "fix": "失败返回 false",
    },
    {
        "api": "UnixFileSystem.getBooleanAttributes0(String) -> int",
        "callers": [("UnixFileSystem.getBooleanAttributes", "int r = ...0(path); return r;", "失败返回 0（不存在）")],
        "violation": "抛异常 -> File.exists 抛未声明异常",
        "fix": "失败返回 0",
    },
]

print("=== hook 的行为契约表（从调用方假设反推）===\n")
for c in CONTRACTS:
    print(f"【{c['api']}】")
    for name, code, assumption in c["callers"]:
        print(f"  调用方 {name}")
        print(f"    {code}")
        print(f"    假设: {assumption}")
    print(f"  [X] 违反后: {c['violation']}")
    print(f"  [OK] 正确行为: {c['fix']}")
    print()

print("=== 结论 ===")
print("两条不同的契约族：")
print("  A) libcore.io.Linux.*  —— 失败【抛 ErrnoException】，绝不返回 null/false 让调用方误判")
print("  B) UnixFileSystem.*0   —— 失败【返回 null/false/0】，绝不抛异常")
print()
print("这两族混用就会崩。v147 到本轮的多次崩溃，本质都是把 B 族的写法用在了 A 族上。")
