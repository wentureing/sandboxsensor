import io

P = "_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
nl = "\r\n" if "\r\n" in raw else "\n"
lines = raw.split(nl)

changed = []

# 1) real_fstat 不存在、GOT 表里也没有 "fstat" -> 直接用 ::fstat
for i, l in enumerate(lines):
    if "real_fstat != nullptr" in l:
        lines[i] = "        r = ::fstat(fd, &st);"
        changed.append("fstat -> ::fstat")

# 2) real_realpath 不存在、GOT 表里也没有 "realpath" -> 直接用 ::realpath
for i, l in enumerate(lines):
    if "real_realpath != nullptr" in l:
        # 形如: const char* got = (real_realpath != nullptr) ? real_realpath(...) : ::realpath(...);
        # 下一行是 : ::realpath(...)
        lines[i] = "    const char* got = ::realpath(real.c_str(), buf);"
        # 删掉紧跟的 "        : ::realpath(...)" 行
        if i + 1 < len(lines) and "::realpath" in lines[i + 1]:
            lines[i + 1] = None
        changed.append("canonicalize0 -> ::realpath")
        break
lines = [l for l in lines if l is not None]

# 3) PATH_MAX 需要 <limits.h>
has_limits = any(l.strip() == "#include <limits.h>" for l in lines)
if not has_limits:
    for i, l in enumerate(lines):
        if l.strip().startswith("#include <"):
            lines[i:i] = ["#include <limits.h>"]
            changed.append("include <limits.h>")
            break

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(nl.join(lines))
print("已修:", changed if changed else "无需修改")

# 验证
with io.open(P, "r", encoding="utf-8", newline="") as f:
    s = f.read()
print("  残留 real_fstat:", s.count("real_fstat"))
print("  残留 real_realpath:", s.count("real_realpath"))
print("  limits.h:", "<limits.h>" in s)
print("  括号:", s.count("{"), s.count("}"))
