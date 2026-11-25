"""排查：还有哪些模块存在「静默失败」风险，需要类似 DebugTrace 记录。

判据（按优先级）：
  P0  返回 String/Boolean/File/Int 且**无任何日志**的「路径相关」函数
      —— 这类函数的错误表现与正常值无法区分（如返回 null/0/false）
  P1  在路径处理链路上，但只有失败日志、没有成功日志
  P2  已有完整日志

只看 main 源码里的 com.nextvm 包，跳过测试与生成代码。
"""
import os
import re

ROOT = "_ref/ShitJumpingVM"

# 关键词：函数名或参数里出现这些，说明与路径/文件系统相关
PATH_WORDS = ("path", "Path", "dir", "Dir", "file", "File", "sdcard", "external",
              "sandbox", "redirect", "translate", "resolve", "open", "list")

FUN_RE = re.compile(
    r"^\s*(?:public |internal |private |protected |override |suspend |inline |operator )*"
    r"fun\s+(?:<[^>]+>\s*)?`?([A-Za-z_][\w]*)`?\s*\(")

results = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    if "/build/" in dirpath or "/src/test" in dirpath or "/.git" in dirpath:
        continue
    for fn in filenames:
        if not fn.endswith(".kt"):
            continue
        full = os.path.join(dirpath, fn)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().replace("\r\n", "\n").split("\n")
        except Exception:
            continue

        for i, line in enumerate(lines):
            m = FUN_RE.match(line)
            if not m:
                continue
            name = m.group(1)
            # 只看路径相关
            if not any(w in name for w in PATH_WORDS):
                continue
            # 取函数体（粗略：往下 30 行）
            body = "\n".join(lines[i:i + 30])
            has_log = ("DebugTrace" in body or "Timber" in body or
                       "GuestBreadcrumb" in body or "Log." in body)
            results.append({
                "file": full.replace(ROOT + os.sep, "").replace("\\", "/"),
                "line": i + 1,
                "name": name,
                "logged": has_log,
            })

print("=== 路径相关函数：有日志 vs 无日志 ===\n")
no_log = [r for r in results if not r["logged"]]
logged = [r for r in results if r["logged"]]
print("有日志: %d 个" % len(logged))
print("无日志: %d 个\n" % len(no_log))

# 按文件聚合"无日志"的
from collections import defaultdict
by_file = defaultdict(list)
for r in no_log:
    by_file[r["file"]].append(r)

print("=== 需要补记录的候选（按文件聚合）===")
for f in sorted(by_file, key=lambda x: -len(by_file[x])):
    items = by_file[f]
    print("\n  %s  (%d 个)" % (f, len(items)))
    for r in items[:12]:
        print("    L%-5d %s" % (r["line"], r["name"]))
    if len(items) > 12:
        print("    ... 其余 %d 个" % (len(items) - 12))
