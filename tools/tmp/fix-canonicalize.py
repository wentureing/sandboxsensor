import io

P = "_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
raw = raw.replace("\r\n", "\n").replace("\r", "\n")
lines = raw.split("\n")

if "canonicalize_logically" in raw:
    print("已应用")
    raise SystemExit(0)

# ---------- 1) 在 hooked_ufs_canonicalize0 之前插入 canonicalize_logically ----------
ci = next(i for i, l in enumerate(lines) if l.startswith("static jstring hooked_ufs_canonicalize0("))

HELPER = '''/*
 * 纯字符串层面的路径规范化：消除 "//"、"/./"、"/../"。
 * 不 touch 文件系统，所以对「实际不存在」的合成路径也能成功。
 *
 * 为什么需要它：合成路径（如 /storage/emulated/0/Android/data/<pkg>/files）
 * 在真机上不存在，realpath 必然失败。但 canonicalize0 的调用方（例如
 * StorageManager.fixupAppDir）只用结果做「路径去重 / 归属判断」，并不要求文件存在。
 * 因此这里返回「规范化后的合成路径」：看起来与 realpath 结果同形，实际不存在，
 * 且绝不把沙箱路径泄露出去。
 */
static std::string canonicalize_logically(const std::string& p) {
    if (p.empty() || p[0] != '/') return p;   // 相对路径原样返回
    std::vector<std::string> segs;
    size_t i = 1;
    while (i <= p.size()) {
        size_t j = p.find('/', i);
        std::string seg = p.substr(i, (j == std::string::npos ? p.size() : j) - i);
        if (seg == "." || seg.empty()) {
            // 跳过
        } else if (seg == "..") {
            if (!segs.empty()) segs.pop_back();
        } else {
            segs.push_back(seg);
        }
        if (j == std::string::npos) break;
        i = j + 1;
    }
    std::string out;
    for (const auto& s : segs) {
        out += "/";
        out += s;
    }
    return out.empty() ? std::string("/") : out;
}

'''.split("\n")
lines[ci:ci] = HELPER

# 重新定位
ci = next(i for i, l in enumerate(lines) if l.startswith("static jstring hooked_ufs_canonicalize0("))

# 找函数体结束
depth = 0
end = None
for i in range(ci, len(lines)):
    depth += lines[i].count("{") - lines[i].count("}")
    if i > ci and depth == 0:
        end = i
        break
assert end is not None

NEW = '''static jstring hooked_ufs_canonicalize0(JNIEnv* env, jclass, jstring jpath, jboolean /*ignore*/) {
    if (jpath == nullptr) return nullptr;

    // 先拿「未翻译的原始输入」—— 合成路径要原样返回它的规范化形式。
    const char* rawChars = env->GetStringUTFChars(jpath, nullptr);
    if (rawChars == nullptr) return nullptr;
    std::string raw(rawChars);
    env->ReleaseStringUTFChars(jpath, rawChars);

    std::string real;
    bool translated = false;
    java_io_resolve(env, jpath, &real, &translated);
    if (real.empty()) return jpath;

    g_java_io_fs_total.fetch_add(1);
    if (translated) g_java_io_fs_translated.fetch_add(1);

    if (translated) {
        // 合成路径分支：**绝不返回沙箱路径，也不抛异常**。
        //
        // 两条都不能做：
        //   * 返回 realpath 结果 -> 那是 <sandbox>/... 直接暴露沙箱
        //   * 失败抛 IOException   -> StorageManager.fixupAppDir 等调用方会报错
        //     （实测日志：Failed to get canonical path for
        //       /storage/emulated/0/Android/data/com.nextvm.app/files/logs）
        //
        // 返回「逻辑规范化后的原始路径」：与 realpath 结果同形、实际不存在、不泄露。
        std::string canonical = canonicalize_logically(raw);
        LOGI("ufs-alias canonicalize0 [synth]: %s -> %s", raw.c_str(), canonical.c_str());
        return env->NewStringUTF(canonical.c_str());
    }

    // 非合成路径：保持 AOSP 语义（realpath 失败抛 IOException）
    char buf[PATH_MAX];
    const char* got = ::realpath(real.c_str(), buf);
    if (got == nullptr) {
        int err = errno;
        g_java_io_fs_fail.fetch_add(1);
        java_io_record("canonicalize0", real.c_str(), err, 0);
        java_io_throw_ioexception(env, real.c_str(), err);
        return nullptr;
    }
    return env->NewStringUTF(buf);
}'''.split("\n")

lines[ci:end + 1] = NEW

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write("\n".join(lines))
print("canonicalize0 已改为：合成路径返回规范化原始路径（不暴露沙箱、不抛异常）")
