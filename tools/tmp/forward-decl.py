import io

P = "_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
raw = raw.replace("\r\n", "\n").replace("\r", "\n")
lines = raw.split("\n")

if "is_container_diag_path_forward" in raw:
    print("已应用")
    raise SystemExit(0)

# 在使用点（hooked_java_linux_open）之前插入前置声明
use_i = next(i for i, l in enumerate(lines) if "is_container_diag_path(real)" in l)
open_i = next(i for i, l in enumerate(lines) if l.startswith("static jobject hooked_java_linux_open("))

DECL = [
    "/* 前置声明：定义在文件更下方，open 的日志过滤会用到 */",
    "static bool is_container_diag_path(const std::string& p);  // is_container_diag_path_forward",
    "",
]
lines[open_i:open_i] = DECL

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write("\n".join(lines))
print("已加前置声明")
