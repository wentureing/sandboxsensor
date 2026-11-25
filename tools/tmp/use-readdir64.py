import io

P = "_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
raw = raw.replace("\r\n", "\n").replace("\r", "\n")
lines = raw.split("\n")

if "readdir64" in raw:
    print("已应用")
    raise SystemExit(0)

# 在 hooked_ufs_list0 内把 readdir 换成 readdir64，struct dirent 换成 struct dirent64
start = next(i for i, l in enumerate(lines) if l.startswith("static jobjectArray hooked_ufs_list0("))
end = None
depth = 0
for i in range(start, len(lines)):
    depth += lines[i].count("{") - lines[i].count("}")
    if i > start and depth == 0:
        end = i
        break
assert end is not None

changed = 0
for i in range(start, end + 1):
    if "struct dirent* e = readdir(d);" in lines[i]:
        lines[i] = lines[i].replace("struct dirent* e = readdir(d);",
                                    "struct dirent64* e = readdir64(d);   // AOSP 用 readdir64；用 readdir 会在 64 位上有 ABI 不匹配")
        changed += 1

assert changed == 1, f"预期改 1 处，实际 {changed}"

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write("\n".join(lines))
print("hooked_ufs_list0 改用 readdir64")
