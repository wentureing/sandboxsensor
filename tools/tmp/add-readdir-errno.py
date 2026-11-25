import io

P = "_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
raw = raw.replace("\r\n", "\n").replace("\r", "\n")
lines = raw.split("\n")

if "readdir err" in raw:
    print("已应用")
    raise SystemExit(0)

# 找 hooked_ufs_list0 里的 readdir 循环
start = next(i for i, l in enumerate(lines) if l.startswith("static jobjectArray hooked_ufs_list0("))
end = None
depth = 0
for i in range(start, len(lines)):
    depth += lines[i].count("{") - lines[i].count("}")
    if i > start and depth == 0:
        end = i
        break
assert end is not None

# 定位 for(;;) 循环
loop_i = None
for i in range(start, end):
    if "readdir(d)" in lines[i]:
        loop_i = i
        break
assert loop_i is not None, "readdir 未找到"

print(f"readdir 调用在 L{loop_i+1}: {lines[loop_i].strip()}")

# 把「if (e == nullptr) break;」替换成带 errno 上报的版本
OLD = "        if (e == nullptr) break;"
NEW = ("        if (e == nullptr) {\n"
       "            // readdir 返回 null 有两种情况：遍历结束（errno 不动/为 0），或出错。\n"
       "            // 旧实现直接 break，把错误当成「目录为空」—— 这是「文件在但列表空」的\n"
       "            // 一个可疑根因，所以这里必须把 errno 打出来。\n"
       "            if (errno != 0) {\n"
       "                int rd_err = errno;\n"
       "                LOGE(\"ufs list0: readdir err=%d (%s) on %s\", rd_err, strerror(rd_err), real.c_str());\n"
       "                g_java_io_fs_fail.fetch_add(1);\n"
       "                java_io_record(\"list0\", real.c_str(), rd_err, 0);\n"
       "            }\n"
       "            break;\n"
       "        }")

hit = False
for i in range(loop_i, min(loop_i + 6, end)):
    if lines[i].strip() == "if (e == nullptr) break;":
        lines[i] = NEW
        hit = True
        break
assert hit, "if (e == nullptr) break; 未找到"

# opendir 成功时也打一条，确认不是打开失败
OLD_OPEN = "    if (d == nullptr) {\n        int err = errno;"
if OLD_OPEN in "\n".join(lines[start:end]):
    pass  # 保持原样

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write("\n".join(lines))
print("已加 readdir errno 上报")
