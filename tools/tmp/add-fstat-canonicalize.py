import io

P = "_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
raw = raw.replace("\r\n", "\n").replace("\r", "\n")
lines = raw.split("\n")

assert "hooked_java_linux_fstat" not in raw, "已应用"

# 插在 hooked_java_linux_stat 之前
ti = next(i for i, l in enumerate(lines) if l.startswith("static jobject hooked_java_linux_stat("))

BLOCK = r'''/* 从 java.io.FileDescriptor 取原始 fd（字段是 private int descriptor）。 */
static int java_io_get_fd(JNIEnv* env, jobject jfd) {
    if (jfd == nullptr) return -1;
    jclass cls = env->GetObjectClass(jfd);
    if (cls == nullptr) return -1;
    jfieldID fid = env->GetFieldID(cls, "descriptor", "I");
    if (fid == nullptr) {
        if (env->ExceptionCheck()) env->ExceptionClear();
        env->DeleteLocalRef(cls);
        return -1;
    }
    jint fd = env->GetIntField(jfd, fid);
    env->DeleteLocalRef(cls);
    return static_cast<int>(fd);
}

/*
 * Libcore.os.fstat(FileDescriptor) -> StructStat
 *
 * 为什么必须有：FileInputStream 构造时会 fstat 一次拿文件长度与类型
 * （IoUtils / IoBridge 的校验路径）。不 hook 它，fd 是沙箱里的 fd，
 * 但 fstat 本身不需要路径，所以不翻译也不会错 —— 真正的问题是
 * 有些调用方会先 stat 路径再 fstat 交叉验证。
 * 这里实现它是为了让「返回宿主形态路径」后整条链没有缺口。
 */
static jobject hooked_java_linux_fstat(JNIEnv* env, jclass, jobject jfd) {
    int fd = java_io_get_fd(env, jfd);
    if (fd < 0) {
        java_io_throw_errno(env, "fstat", EBADF, "");
        return nullptr;
    }
    g_java_io_fs_total.fetch_add(1);

    struct stat st;
    int r;
    do {
        r = (real_fstat != nullptr) ? real_fstat(fd, &st) : ::fstat(fd, &st);
    } while (r == -1 && errno == EINTR);

    if (r == -1) {
        int err = errno;
        g_java_io_fs_fail.fetch_add(1);
        java_io_record("fstat", "(fd)", err, 0);
        java_io_throw_errno(env, "fstat", err, "");
        return nullptr;
    }
    jobject obj = java_io_make_struct_stat(env, st);
    if (obj != nullptr) {
        LOGI("ufs-alias fstat: fd=%d -> size=%lld mode=0%o", fd,
             static_cast<long long>(st.st_size),
             static_cast<unsigned>(st.st_mode & 07777));
    }
    return obj;
}

/*
 * UnixFileSystem.canonicalize0(String, boolean) -> String
 *
 * AOSP: 失败时 JNU_ThrowIOExceptionWithLastError(env, "Bad pathname")，不返回 null。
 * 这个方法是「返回宿主形态路径」的**前置条件**：
 * 若不 hook，getCanonicalPath() 会给出真实的沙箱绝对路径（/data/user/0/<宿主>/files/virtual/...），
 * 比现在的暴露更彻底。
 */
static jstring hooked_ufs_canonicalize0(JNIEnv* env, jclass, jstring jpath, jboolean /*ignore*/) {
    if (jpath == nullptr) return nullptr;
    std::string real;
    bool translated = false;
    java_io_resolve(env, jpath, &real, &translated);
    if (real.empty()) return jpath;

    g_java_io_fs_total.fetch_add(1);
    if (translated) g_java_io_fs_translated.fetch_add(1);

    char buf[PATH_MAX];
    const char* got = (real_realpath != nullptr) ? real_realpath(real.c_str(), buf)
                                                 : ::realpath(real.c_str(), buf);
    if (got == nullptr) {
        int err = errno;
        g_java_io_fs_fail.fetch_add(1);
        java_io_record("canonicalize0", real.c_str(), err, 0);
        java_io_throw_ioexception(env, real.c_str(), err);
        return nullptr;
    }
    LOGI("ufs-alias canonicalize0%s: %s -> %s", translated ? " [xlat]" : "",
         real.c_str(), buf);
    return env->NewStringUTF(buf);
}

'''.split("\n")

lines[ti:ti] = BLOCK

# 注册：在 lstat 之后加 fstat；在 UnixFileSystem 注册表里加 canonicalize0
idx = None
for i, l in enumerate(lines):
    if 'const_cast<char*>("lstat")' in l:
        for j in range(i, i + 4):
            if "hooked_java_linux_lstat" in lines[j]:
                idx = j
                break
        if idx:
            break
assert idx is not None, "lstat 注册项未找到"
lines[idx + 1:idx + 1] = [
    '        {const_cast<char*>("fstat"),',
    '         const_cast<char*>("(Ljava/io/FileDescriptor;)Landroid/system/StructStat;"),',
    '         reinterpret_cast<void*>(hooked_java_linux_fstat)},',
]

# UnixFileSystem 表加 canonicalize0
idx2 = None
for i, l in enumerate(lines):
    if 'const_cast<char*>("setReadOnly0")' in l:
        for j in range(i, i + 4):
            if "hooked_ufs_setReadOnly0" in lines[j]:
                idx2 = j
                break
        if idx2:
            break
assert idx2 is not None, "setReadOnly0 注册项未找到"
lines[idx2 + 1:idx2 + 1] = [
    '                {const_cast<char*>("canonicalize0"),',
    '                 const_cast<char*>("(Ljava/lang/String;Z)Ljava/lang/String;"),',
    '                 reinterpret_cast<void*>(hooked_ufs_canonicalize0)},',
]

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write("\n".join(lines))
print("已加 fstat + canonicalize0")
