import io

P = "_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
raw = raw.replace("\r\n", "\n").replace("\r", "\n")

if "is_container_diag_path" in raw:
    print("已应用")
    raise SystemExit(0)

# 1) 加一个判断容器自身诊断文件的辅助函数，插在 ufs_get_path 之前
anchor_fn = None
for name in ("static jstring ufs_get_path(", "static void java_io_resolve("):
    if name in raw:
        anchor_fn = name
        break
assert anchor_fn, "锚点函数未找到"

HELPER = '''/*
 * 容器自己的诊断日志文件：DebugTrace / Diagnostics / Breadcrumb 都写在
 * <containerDataDir>/files/ 下。guest 进程里这些文件同样会被 hooked_java_linux_open
 * 与 hooked_ufs_list0 拦到，且写入频率极高（每次 DebugTrace.detail 都 open 一次）。
 *
 * 不排除它们的话，前 200 条 hook 日志会被 debug-trace.log 占满，
 * 真正想看的 guest 文件访问反而看不到 —— 实测过：整页都是
 * `JavaIo open: .../debug-trace.log (flags=0x441)`。
 */
static bool is_container_diag_path(const std::string& p) {
    static const char* kMarkers[] = {
        "/debug-trace.log", "/checks.log", "/breadcrumb-",
        "/stackdump-", "/webviewwarm-", "/webviewwatch-",
    };
    for (const char* m : kMarkers) {
        if (p.find(m) != std::string::npos) return true;
    }
    return false;
}

'''
raw = raw.replace(anchor_fn, HELPER + anchor_fn, 1)

# 2) 在 hooked_java_linux_open 的日志处加排除
OLD_LOG = '''    int n = ++g_java_io_open_logs;
    g_java_io_open_total.fetch_add(1);
    if (n <= 200) {
        LOGI("JavaIo open%s: %s (flags=0x%x)",
             translated ? " [xlat]" : "", real.c_str(),
             static_cast<unsigned>(flags));
    }'''
NEW_LOG = '''    g_java_io_open_total.fetch_add(1);
    // 容器自己的诊断文件不计入日志配额，否则会把真正想看的 guest 访问挤掉
    if (!is_container_diag_path(real)) {
        int n = ++g_java_io_open_logs;
        if (n <= 200) {
            LOGI("JavaIo open%s: %s (flags=0x%x)",
                 translated ? " [xlat]" : "", real.c_str(),
                 static_cast<unsigned>(flags));
        }
    }'''
assert OLD_LOG in raw, "open 日志段未匹配"
raw = raw.replace(OLD_LOG, NEW_LOG, 1)

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(raw)
print("已加 is_container_diag_path，open 日志跳过诊断文件")
