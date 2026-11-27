// 路径探针（native 层）
//
// 目的：测量 guest 在「直接安装」与「容器内运行」两种场景下，
//      libc 层看到的真实路径、fd、/proc 视图。
//
// 返回格式（多行文本，供 Java 侧解析）：
//   <id>\t<status:ok|fail>\t<value>
//
// 设计约束：
//   * 只做读取与最小写操作，全部落在 dataDir/files 下，不污染设备；
//   * 每项独立 try，单项失败不影响其他项；
//   * 不修改任何全局状态。

#include <jni.h>
#include <android/log.h>

#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <string>

namespace {

std::string g_out;

void add(const std::string& id, bool ok, const std::string& value) {
    g_out += id;
    g_out += '\t';
    g_out += (ok ? "ok" : "fail");
    g_out += '\t';
    g_out += value;
    g_out += '\n';
    __android_log_print(ANDROID_LOG_INFO, "PROBE", "native %s %s %s", id.c_str(),
                        ok ? "ok" : "fail", value.c_str());
}

std::string JStr(JNIEnv* env, jstring s) {
    if (s == nullptr) return {};
    const char* c = env->GetStringUTFChars(s, nullptr);
    std::string r = (c != nullptr) ? c : "";
    if (c != nullptr) env->ReleaseStringUTFChars(s, c);
    return r;
}

std::string Errcode(const char* what) {
    return std::string(what) + " errno=" + std::to_string(errno) + "(" + strerror(errno) + ")";
}

// 读取整个小文件（用于 /proc/self/cmdline）
std::string ReadSmallFile(const char* path, size_t maxLen) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return {};
    char buf[512];
    ssize_t n = read(fd, buf, sizeof(buf) - 1 < maxLen ? sizeof(buf) - 1 : maxLen);
    close(fd);
    if (n <= 0) return {};
    buf[n] = '\0';
    return std::string(buf, static_cast<size_t>(n));
}

}  // namespace

extern "C" JNIEXPORT jstring JNICALL
Java_com_sandbox_probe_NativeProbe_runAll(JNIEnv* env, jclass /*clazz*/, jstring jDataDir,
                                          jstring jPkg) {
    const std::string dataDir = JStr(env, jDataDir);  // 例如 /data/data/com.sandbox.probe
    const std::string pkg = JStr(env, jPkg);
    g_out.clear();

    // ---- N01 真实身份 ----
    {
        char buf[128];
        snprintf(buf, sizeof(buf), "uid=%d euid=%d gid=%d pid=%d ppid=%d", getuid(), geteuid(),
                 getgid(), getpid(), getppid());
        add("N01.identity", true, buf);
    }

    // ---- N02 /proc/self/cmdline ----
    {
        std::string cmd = ReadSmallFile("/proc/self/cmdline", 256);
        for (char& c : cmd) {
            if (c == '\0') c = '|';
        }
        add("N02.proc_cmdline", !cmd.empty(), cmd.empty() ? std::string("empty") : cmd);
    }

    // ---- N03 open 字面量路径（L1/L2 关键用例）----
    const std::string target = dataDir + "/files/native_probe.txt";
    int fd = -1;
    {
        fd = open(target.c_str(), O_CREAT | O_RDWR | O_CLOEXEC, 0600);
        add("N03.open_literal", fd >= 0,
            fd >= 0 ? ("fd=" + std::to_string(fd) + " path=" + target) : Errcode("open"));
    }

    // ---- N04 stat ----
    {
        struct stat st {};
        int rc = stat(target.c_str(), &st);
        char buf[256];
        if (rc == 0) {
            snprintf(buf, sizeof(buf), "size=%lld mode=%o uid=%d gid=%d dev=%llu ino=%llu",
                     static_cast<long long>(st.st_size), st.st_mode, st.st_uid, st.st_gid,
                     static_cast<unsigned long long>(st.st_dev),
                     static_cast<unsigned long long>(st.st_ino));
            add("N04.stat", true, buf);
        } else {
            add("N04.stat", false, Errcode("stat"));
        }
    }

    // ---- N05 access ----
    {
        bool ok = access((dataDir + "/files").c_str(), F_OK) == 0;
        add("N05.access_files_dir", ok, ok ? "F_OK ok" : Errcode("access"));
    }

    // ---- N06 fopen/fwrite（stdio 路径）----
    {
        FILE* fp = fopen(target.c_str(), "w");
        bool ok = false;
        if (fp != nullptr) {
            ok = fputs("probe", fp) >= 0;
            fclose(fp);
        }
        add("N06.fopen_write", ok, ok ? "wrote 5 bytes" : Errcode("fopen"));
    }

    // ---- N07 openat 相对路径（fd 相对，最容易漏的一类）----
    {
        int dfd = open((dataDir + "/files").c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        if (dfd >= 0) {
            mkdirat(dfd, "rel", 0700);
            int rfd = openat(dfd, "rel/child.txt", O_CREAT | O_RDWR | O_CLOEXEC, 0600);
            add("N07.openat_relative", rfd >= 0,
                rfd >= 0 ? ("dfd=" + std::to_string(dfd) + " fd=" + std::to_string(rfd))
                         : Errcode("openat"));
            if (rfd >= 0) close(rfd);
            close(dfd);
        } else {
            add("N07.openat_relative", false, Errcode("open_dir"));
        }
    }

    // ---- N08 readlink /proc/self/fd（是否泄露真实路径）----
    {
        if (fd >= 0) {
            char link[512];
            const std::string p = "/proc/self/fd/" + std::to_string(fd);
            ssize_t n = readlink(p.c_str(), link, sizeof(link) - 1);
            if (n > 0) {
                link[n] = '\0';
                add("N08.readlink_fd", true, link);
            } else {
                add("N08.readlink_fd", false, Errcode("readlink"));
            }
        } else {
            add("N08.readlink_fd", false, "no fd from N03");
        }
    }

    // ---- N09 直接 syscall（libc.syscall 导出符号，可被 GOT 拦截）----
    {
        int sfd = static_cast<int>(
            syscall(SYS_openat, AT_FDCWD, (dataDir + "/files/syscall.txt").c_str(),
                    O_CREAT | O_RDWR | O_CLOEXEC, 0600));
        add("N09.syscall_openat", sfd >= 0,
            sfd >= 0 ? ("fd=" + std::to_string(sfd)) : Errcode("syscall"));
        if (sfd >= 0) close(sfd);
    }

    // ---- N10 /proc/self/maps 特征 ----
    {
        FILE* fp = fopen("/proc/self/maps", "r");
        if (fp == nullptr) {
            add("N10.maps_scan", false, Errcode("fopen_maps"));
        } else {
            int lines = 0, baseApk = 0, pkgHit = 0, probeLib = 0;
            std::string firstBaseApk;
            char line[1024];
            while (fgets(line, sizeof(line), fp) != nullptr) {
                ++lines;
                if (strstr(line, "base.apk") != nullptr) {
                    ++baseApk;
                    if (firstBaseApk.empty()) firstBaseApk = line;
                }
                if (!pkg.empty() && strstr(line, pkg.c_str()) != nullptr) ++pkgHit;
                if (strstr(line, "libprobe.so") != nullptr) ++probeLib;
            }
            fclose(fp);
            char buf[512];
            snprintf(buf, sizeof(buf), "lines=%d base_apk=%d pkg_hit=%d probe_lib=%d first_base_apk=%s",
                     lines, baseApk, pkgHit, probeLib, firstBaseApk.empty() ? "-" : firstBaseApk.c_str());
            add("N10.maps_scan", true, buf);
        }
    }

    // ---- N16 /proc/self/maps 经 libc open/read（应经过容器的 GOT hook）----
    // 与 N10（fopen 路径）形成对照：容器里 N16 看到的是伪造视图（pkg_hit/nextvm_hit 低），
    // 直装（无容器）时两者一致。加这一项同时覆盖 native 侧 /proc spoof 的插桩计数。
    {
        int mfd = open("/proc/self/maps", O_RDONLY | O_CLOEXEC);
        if (mfd < 0) {
            add("N16.libc_maps", false, Errcode("open_maps"));
        } else {
            std::string all;
            char buf[4096];
            ssize_t n;
            while ((n = read(mfd, buf, sizeof(buf))) > 0) {
                all.append(buf, static_cast<size_t>(n));
            }
            close(mfd);
            int lines = 0, pkgHit = 0, nextvmHit = 0;
            size_t pos = 0;
            while (pos < all.size()) {
                size_t e = all.find('\n', pos);
                if (e == std::string::npos) e = all.size();
                ++lines;
                if (!pkg.empty() && all.find(pkg, pos) < e) ++pkgHit;
                if (all.find("nextvm", pos) < e) ++nextvmHit;
                pos = e + 1;
            }
            char out[256];
            snprintf(out, sizeof(out), "lines=%d pkg_hit=%d nextvm_hit=%d", lines, pkgHit,
                     nextvmHit);
            add("N16.libc_maps", true, out);
        }
    }

    // ---- N11 目录/写操作组合（很多实现只拦读）----
    {
        const std::string d = dataDir + "/files/n11";
        bool mk = (mkdir(d.c_str(), 0700) == 0) || errno == EEXIST;
        const std::string a = d + "/a.txt";
        const std::string b = d + "/b.txt";
        int wfd = open(a.c_str(), O_CREAT | O_RDWR | O_CLOEXEC, 0600);
        if (wfd >= 0) close(wfd);
        bool rn = rename(a.c_str(), b.c_str()) == 0;
        bool un = unlink(b.c_str()) == 0;
        bool rm = rmdir(d.c_str()) == 0;
        char buf[256];
        snprintf(buf, sizeof(buf), "mkdir=%d rename=%d unlink=%d rmdir=%d", mk, rn, un, rm);
        add("N11.write_ops", mk && rn && un && rm, buf);
    }

    // ---- N12 getuid 与 Java myUid 的对照由 Java 侧合并，这里补 euid 的 syscall 形态 ----
    {
        long uid = syscall(SYS_getuid);
        add("N12.syscall_getuid", true, "uid=" + std::to_string(uid));
    }

    // ---- N13 / N14 / N15：另一种等价前缀 /data/user/0/<pkg> ----
    // 真实设备上 realpath("/data/user/0/x") 会解析成 "/data/data/x"（见 T1 报告 J30），
    // 因此容器若只映射其中一个前缀，另一个前缀的访问会绕过映射 —— 这三条专门测它。
    {
        const std::string user0 = "/data/user/0/" + pkg + "/files";
        mkdir(user0.c_str(), 0700);
        const std::string target2 = user0 + "/native_user0.txt";
        int ufd = open(target2.c_str(), O_CREAT | O_RDWR | O_CLOEXEC, 0600);
        add("N13.open_user0_prefix", ufd >= 0,
            ufd >= 0 ? ("fd=" + std::to_string(ufd) + " path=" + target2)
                     : Errcode("open_user0"));
        if (ufd >= 0) {
            char link[512];
            const std::string p = "/proc/self/fd/" + std::to_string(ufd);
            ssize_t n = readlink(p.c_str(), link, sizeof(link) - 1);
            if (n > 0) {
                link[n] = '\0';
                add("N14.readlink_user0_fd", true, link);
            } else {
                add("N14.readlink_user0_fd", false, Errcode("readlink_user0"));
            }
            close(ufd);
        } else {
            add("N14.readlink_user0_fd", false, "no fd from N13");
        }
    }
    {
        const std::string p = "/data/user/0/" + pkg + "/files";
        struct stat st {};
        int rc = stat(p.c_str(), &st);
        if (rc == 0) {
            char buf[128];
            snprintf(buf, sizeof(buf), "mode=%o uid=%d dev=%llu", st.st_mode, st.st_uid,
                     static_cast<unsigned long long>(st.st_dev));
            add("N15.stat_user0_dir", true, buf);
        } else {
            add("N15.stat_user0_dir", false, Errcode("stat_user0"));
        }
    }

    // ---- P28 导入通道写越权探测（sandboxsensor）----
    // 目的：验证 is_import_read_exempt 是否让 guest 能写**真实的** /sdcard/Download。
    //
    // 为什么用 fopen 而不是 open：
    //   p28 给 hooked_fopen 加了豁免，条件是 mode[0]=='r'，而 "r+" 也满足 ->
    //   理论上会被放行。而写走的是 fwrite -> write()，write 没有被 hook，
    //   所以"放行 fopen"能否变成"真的写进去"必须实测。
    //
    // 判据不在本函数里：外部用 adb 读 /sdcard/Download/probe-wtest.txt
    //   仍是 ORIGINAL -> 写被拦（缺陷不成立 / 修复生效）
    //   变成 HACKED   -> 真实文件被改（缺陷成立）
    // 这里只记录 fopen/fwrite 的返回值，供三版对比（p26 无豁免 / p28 有缺陷 / p29 已修）。
    {
        const char* wt = "/sdcard/Download/probe-wtest.txt";
        std::string r;

        FILE* fr = fopen(wt, "r");
        r += "fopen_r=";
        r += fr ? "ok" : Errcode("fopen_r");
        if (fr) fclose(fr);

        FILE* frp = fopen(wt, "r+");
        r += " fopen_rplus=";
        r += frp ? "ok" : Errcode("fopen_rplus");
        if (frp) {
            size_t w = fwrite("HACKED", 1, 6, frp);
            fflush(frp);
            fclose(frp);
            r += " fwrite=" + std::to_string(w);
        }

        // 用 stat 看真实文件大小，作为第二判据（容器外还会再核一次内容）
        struct stat wst {};
        if (stat(wt, &wst) == 0) {
            r += " size=" + std::to_string(static_cast<long long>(wst.st_size));
        } else {
            r += " size=" + Errcode("stat_wtest");
        }

        add("P28.sdcard_write", frp != nullptr, r);
    }

    if (fd >= 0) close(fd);
    return env->NewStringUTF(g_out.c_str());
}
