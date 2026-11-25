package com.sandbox.probe;

import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.os.Build;
import android.os.Environment;
import android.os.Process;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.util.List;

/**
 * 全量探针。
 *
 * 覆盖 06 文档 §8 的验收用例与 07 文档 §5 的「6 组入口」：
 *   Context / ApplicationInfo / PackageManager / java.io / libc(native) / 设备身份
 *
 * 设计约束：
 *   * 每条独立 try，单项失败不影响其余；
 *   * 只写 dataDir / files 下的文件，不污染设备；
 *   * 不申请任何敏感权限，保证「直接安装」与「容器内」行为可比。
 */
public final class ProbeRunner {

    private final Context ctx;
    private final JSONArray entries = new JSONArray();
    private final String pkg;
    /** 本轮只跑重点：默认 round（路径 J* / 写入 F* / 诊断 D*），其余记 skipped。 */
    private final ProbeSuite suite = ProbeSuite.current();

    public ProbeRunner(Context ctx) {
        this.ctx = ctx;
        this.pkg = ctx.getPackageName();
    }

    public JSONObject run() throws Exception {
        probeContextDirs();
        probeExternal();
        probeApplicationInfo();
        probePackageManager();
        probeIdentity();
        probeLiteralPaths();
        probeCanonical();
        probeJavaIo();
        probeDevice();
        // ---- 新增 7 个大类（与上面 10 类维度互不重叠）----
        probeProcStat();   // P01-P04  /proc 进程与 UID 视角
        probeMaps();       // P05-P08  maps 内容分析
        probeSoLoad();     // P09-P12  SO 加载链路（验证 B1）
        probeCrossPkg();   // P13-P16  跨包可见性 / 逃逸面
        probeSysProps();   // P17-P19  JVM 系统属性
        probeFd();         // P20-P21  fd 视图（已打开 fd 的真实路径）
        probeFs();         // F01-F08  文件系统读写能力（验 mkdir/opendir/rename/unlink 重定向）
        probeMount();      // P22-P23  mountinfo
        probeUiEntries();  // P24-P27  uiprobe（PopupWindow / 子窗口，来自 UiProbe）
        probeNative();
        probeDiag();

        JSONObject root = new JSONObject();
        root.put("schema", 1);
        root.put("suite", suite.name());
        root.put("mode", detectMode());
        root.put("packageName", pkg);
        root.put("timeline", Report.timeline());
        root.put("provider", Report.providerEntries());
        root.put("entries", entries);
        return root;
    }

    // ---------------------------------------------------------------- diag (D)

    /**
     * D01-D05：诊断通道是否按预期产出。
     *
     * 命名规则与 core:common 的 DiagFiles 保持一致（探针不依赖 core:common，
     * 规范文本在 DiagFiles 的 KDoc 里作为唯一来源）。
     */
    private void probeDiag() {
        final File files = ctx.getFilesDir();
        final int pid = android.os.Process.myPid();

        put("D01", "diag", "breadcrumb.exists", () -> {
            File f = new File(files, "breadcrumb-" + pid + ".log");
            return "exists=" + f.exists() + " size=" + (f.exists() ? f.length() : 0L);
        });
        put("D02", "diag", "checks.lines", () -> {
            File f = new File(files, "checks.log");
            if (!f.exists()) return "exists=false";
            String s = readText(f, 64 * 1024);
            int lines = s.isEmpty() ? 0 : s.split("\n", -1).length;
            return "exists=true lines=" + lines;
        });
        put("D03", "diag", "stackdump.exists", () -> {
            File f = new File(files, "stackdump-" + pid + ".log");
            return "exists=" + f.exists() + " size=" + (f.exists() ? f.length() : 0L);
        });
        put("D04", "diag", "log.total", () -> {
            File[] fs = files.listFiles();
            int n = 0;
            long bytes = 0L;
            if (fs != null) {
                for (File f : fs) {
                    String nm = f.getName();
                    if (f.isFile() && (nm.endsWith(".log") || nm.endsWith(".dex"))) {
                        n++;
                        bytes += f.length();
                    }
                }
            }
            return "files=" + n + " bytes=" + bytes;
        });
        put("D05", "diag", "checks.tail", () -> {
            File f = new File(files, "checks.log");
            if (!f.exists()) return "exists=false";
            String s = readText(f, 64 * 1024);
            String[] all = s.split("\n");
            int from = Math.max(0, all.length - 3);
            StringBuilder sb = new StringBuilder();
            for (int i = from; i < all.length; i++) {
                if (sb.length() > 0) sb.append(" | ");
                String line = all[i];
                sb.append(line.length() > 120 ? line.substring(0, 120) : line);
            }
            return sb.toString();
        });
    }

    // ---------------------------------------------------------------- context

    private void probeContextDirs() {
        put("J01", "context", "getDataDir", () -> ctx.getDataDir().getAbsolutePath());
        put("J02", "context", "getFilesDir", () -> {
            File f = ctx.getFilesDir();
            return f.getAbsolutePath() + " exists=" + f.exists() + " canWrite=" + f.canWrite();
        });
        put("J03", "context", "getCacheDir", () -> String.valueOf(ctx.getCacheDir()));
        put("J04", "context", "getCodeCacheDir", () -> String.valueOf(ctx.getCodeCacheDir()));
        put("J05", "context", "getNoBackupFilesDir", () -> String.valueOf(ctx.getNoBackupFilesDir()));
        put("J06", "context", "getDir(probe)", () -> String.valueOf(ctx.getDir("probe", Context.MODE_PRIVATE)));
        put("J07", "context", "getDatabasePath+sqlite", () -> {
            File dbFile = ctx.getDatabasePath("probe.db");
            SQLiteDatabase db = SQLiteDatabase.openOrCreateDatabase(dbFile, null);
            try {
                db.execSQL("CREATE TABLE IF NOT EXISTS t(v TEXT)");
                db.execSQL("INSERT INTO t VALUES('probe')");
                try (Cursor c = db.rawQuery("SELECT COUNT(*) FROM t", null)) {
                    c.moveToFirst();
                    return dbFile.getAbsolutePath() + " rows=" + c.getInt(0);
                }
            } finally {
                db.close();
            }
        });
    }

    private void probeExternal() {
        put("J08", "external", "getExternalFilesDir", () -> String.valueOf(ctx.getExternalFilesDir(null)));
        put("J09", "external", "getExternalFilesDirs.count", () -> {
            File[] fs = ctx.getExternalFilesDirs(null);
            return String.valueOf(fs == null ? -1 : fs.length);
        });
        put("J10", "external", "getExternalCacheDir", () -> String.valueOf(ctx.getExternalCacheDir()));
        put("J11", "external", "getExternalMediaDirs[0]", () -> {
            File[] fs = ctx.getExternalMediaDirs();
            return (fs == null || fs.length == 0) ? "null" : fs[0].getAbsolutePath();
        });
        put("J12", "external", "getObbDir", () -> String.valueOf(ctx.getObbDir()));
        put("J13", "external", "Environment.getExternalStorageDirectory",
                () -> String.valueOf(Environment.getExternalStorageDirectory()));
    }

    private void probeApplicationInfo() {
        put("J14", "appinfo", "dataDir", () -> ctx.getApplicationInfo().dataDir);
        put("J15", "appinfo", "sourceDir", () -> {
            String p = ctx.getApplicationInfo().sourceDir;
            return p + " exists=" + new File(p).exists() + " canRead=" + new File(p).canRead();
        });
        put("J16", "appinfo", "nativeLibraryDir", () -> {
            String p = ctx.getApplicationInfo().nativeLibraryDir;
            return p + " exists=" + (p != null && new File(p).exists());
        });
        put("J17", "appinfo", "splitSourceDirs", () -> {
            String[] s = ctx.getApplicationInfo().splitSourceDirs;
            return s == null ? "null" : (s.length + ":" + String.join(",", s));
        });
        put("J18", "appinfo", "deviceProtectedDataDir", () -> String.valueOf(ctx.getApplicationInfo().deviceProtectedDataDir));
    }

    private void probePackageManager() {
        put("J19", "pm", "getApplicationInfo.dataDir", () -> {
            ApplicationInfo ai = ctx.getPackageManager().getApplicationInfo(pkg, 0);
            return ai.dataDir + " uid=" + ai.uid;
        });
        put("J20", "pm", "getPackageInfo", () -> {
            PackageInfo pi = ctx.getPackageManager().getPackageInfo(pkg, 0);
            return "versionName=" + pi.versionName + " versionCode=" + pi.getLongVersionCode()
                    + " targetSdk=" + pi.applicationInfo.targetSdkVersion;
        });
        put("J21", "pm", "getInstalledPackages.count",
                () -> String.valueOf(ctx.getPackageManager().getInstalledPackages(0).size()));
    }

    private void probeIdentity() {
        put("J22", "identity", "myUid vs appInfo.uid", () -> {
            int my = Process.myUid();
            int official = ctx.getApplicationInfo().uid;
            return "myUid=" + my + " officialUid=" + official + " equal=" + (my == official);
        });
        put("J23", "identity", "pid/tid/process", () ->
                "pid=" + Process.myPid() + " tid=" + Process.myTid() + " process=" + Report.currentProcessName());
        put("J24", "identity", "Build", () ->
                "MANUFACTURER=" + Build.MANUFACTURER + " MODEL=" + Build.MODEL
                        + " SDK=" + Build.VERSION.SDK_INT + " RELEASE=" + Build.VERSION.RELEASE
                        + " FINGERPRINT=" + Build.FINGERPRINT);
        put("J25", "identity", "SELinux context", () -> readText(new File("/proc/self/attr/current"), 256));
    }

    // ------------------------------------------------------------ literal path

    private void probeLiteralPaths() {
        put("J26", "literal", "/data/data/<pkg>/files", () -> literalRoundTrip("/data/data/" + pkg));
        put("J27", "literal", "/data/user/0/<pkg>/files", () -> literalRoundTrip("/data/user/0/" + pkg));
        put("J28", "literal", "exists(/data/data/<pkg>)",
                () -> String.valueOf(new File("/data/data/" + pkg).exists()));
        put("J29", "literal", "listFiles(/data/data/<pkg>)", () -> {
            String[] names = new File("/data/data/" + pkg).list();
            return names == null ? "null" : (names.length + ":" + String.join(",", names));
        });
    }

    private String literalRoundTrip(String root) throws Exception {
        File dir = new File(root, "files");
        boolean mk = dir.exists() || dir.mkdirs();
        File f = new File(dir, "literal.txt");
        try (FileOutputStream fos = new FileOutputStream(f)) {
            fos.write("literal".getBytes("UTF-8"));
        }
        byte[] buf = new byte[32];
        int n;
        try (FileInputStream fis = new FileInputStream(f)) {
            n = fis.read(buf);
        }
        return "mkdirs=" + mk + " exists=" + f.exists() + " read=" + new String(buf, 0, Math.max(n, 0), "UTF-8")
                + " path=" + f.getAbsolutePath();
    }

    private void probeCanonical() {
        put("J30", "canonical", "filesDir canonical vs absolute", () -> {
            File f = ctx.getFilesDir();
            return "absolute=" + f.getAbsolutePath() + " canonical=" + f.getCanonicalPath();
        });
        put("J31", "canonical", "not-exist literal canonical",
                () -> new File("/data/user/0/" + pkg + "/files/not_exist_probe").getCanonicalPath());
    }

    private void probeJavaIo() {
        put("J32", "javaio", "filesDir.listFiles.count", () -> {
            File[] fs = ctx.getFilesDir().listFiles();
            return fs == null ? "null" : String.valueOf(fs.length);
        });
        put("J33", "javaio", "SharedPreferences file", () -> {
            SharedPreferences sp = ctx.getSharedPreferences("probe_prefs", Context.MODE_PRIVATE);
            sp.edit().putString("k", "v").commit();
            File f = new File(ctx.getDataDir(), "shared_prefs/probe_prefs.xml");
            return f.getAbsolutePath() + " exists=" + f.exists();
        });
        put("J34", "javaio", "dataDir.list.count", () -> {
            File[] fs = ctx.getDataDir().listFiles();
            if (fs == null) {
                return "null";
            }
            List<String> names = new java.util.ArrayList<>();
            for (File f : fs) {
                names.add(f.getName());
            }
            java.util.Collections.sort(names);
            return names.size() + ":" + String.join(",", names);
        });
    }

    private void probeDevice() {
        put("J35", "device", "pageSize", () -> {
            // 通过 native 侧不可用，这里用反射读 Os.sysconf? 简化：读 /proc/self/smaps 不必要，
            // 直接给出 Build 层面的信息，page size 由 native N13 报告。
            return "SUPPORTED_ABIS=" + String.join(",", Build.SUPPORTED_ABIS);
        });
        put("J36", "device", "supported 64bit abis",
                () -> String.join(",", Build.SUPPORTED_64_BIT_ABIS));
    }

    // ------------------------------------------------- proc/uid 视角（新大类）

    /**
     * 新大类 #1：/proc 的进程与 UID 视角。
     * 与既有 identity 类的区别：identity 走 Java API（Process.myUid），
     * 这里直接读内核 /proc 文件，能看到「容器没有代理到的地方」。
     */
    private void probeProcStat() {
        put("P01", "procstat", "/proc/self/status Uid/Gid", () -> {
            StringBuilder sb = new StringBuilder();
            for (String line : readText(new File("/proc/self/status"), 8192).split("\n")) {
                if (line.startsWith("Uid:") || line.startsWith("Gid:")
                        || line.startsWith("Groups:") || line.startsWith("NSpid:")) {
                    sb.append(line.trim().replaceAll("\\s+", " ")).append(" | ");
                }
            }
            return sb.toString();
        });
        put("P02", "procstat", "/proc/self/cmdline", () -> {
            byte[] b = new byte[512];
            int n;
            try (FileInputStream fis = new FileInputStream("/proc/self/cmdline")) {
                n = fis.read(b);
            }
            if (n <= 0) {
                return "empty";
            }
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < n; i++) {
                char c = (char) (b[i] & 0xff);
                sb.append(c == '\0' ? ' ' : c);
            }
            return sb.toString().trim();
        });
        put("P03", "procstat", "same-uid process count", () -> {
            // 对应公开检测手段 1（AntiVirtualApp：ps | grep <uid_name>）
            int myUid = Process.myUid();
            int count = 0;
            StringBuilder names = new StringBuilder();
            File[] procs = new File("/proc").listFiles();
            if (procs == null) {
                return "cannot list /proc";
            }
            for (File p : procs) {
                String pid = p.getName();
                boolean numeric = pid.length() > 0;
                for (int i = 0; i < pid.length(); i++) {
                    if (pid.charAt(i) < '0' || pid.charAt(i) > '9') {
                        numeric = false;
                        break;
                    }
                }
                if (!numeric) {
                    continue;
                }
                try {
                    String st = readText(new File(p, "status"), 2048);
                    String uidLine = null;
                    String nameLine = null;
                    for (String l : st.split("\n")) {
                        if (l.startsWith("Uid:")) {
                            uidLine = l.trim();
                        } else if (l.startsWith("Name:")) {
                            nameLine = l.trim();
                        }
                    }
                    // /proc/<pid>/status 的 Uid 行是 tab 分隔：`Uid:\t10309\t10309...`
                    // 用空白分割后逐字段比较，否则 indexOf(" <uid> ") 永远匹配不到。
                    boolean uidMatch = false;
                    if (uidLine != null) {
                        String[] f = uidLine.split("\\s+");
                        for (int i = 1; i < f.length; i++) {
                            if (String.valueOf(myUid).equals(f[i])) {
                                uidMatch = true;
                                break;
                            }
                        }
                    }
                    if (uidMatch) {
                        count++;
                        if (names.length() < 140) {
                            names.append(nameLine).append(";");
                        }
                    }
                } catch (Throwable ignore) {
                    // 其他进程的 status 可能不可读，忽略
                }
            }
            return "myUid=" + myUid + " count=" + count + " names=" + names;
        });
        put("P04", "procstat", "/proc/self/task thread count", () -> {
            File task = new File("/proc/self/task");
            String[] list = task.list();
            return "threads=" + (list == null ? -1 : list.length);
        });
    }

    // -------------------------------------------------- maps 内容（新大类）

    /**
     * 新大类 #2：/proc/self/maps 的「内容」而不是「可读性」。
     * native 的 N10 只验证能读到 maps；这里分析内容暴露了什么。
     */
    private void probeMaps() {
        put("P05", "maps", "host pkg hits in maps", () -> {
            String maps = readText(new File("/proc/self/maps"), 1 << 20);
            int total = 0;
            int hostHit = 0;
            int sandboxHit = 0;
            for (String line : maps.split("\n")) {
                if (line.isEmpty()) {
                    continue;
                }
                total++;
                if (line.contains("com.nextvm.app")) {
                    hostHit++;
                }
                if (line.contains("/virtual/")) {
                    sandboxHit++;
                }
            }
            return "lines=" + total + " hostHits=" + hostHit + " sandboxHits=" + sandboxHit;
        });
        put("P06", "maps", "so paths sample", () -> {
            String maps = readText(new File("/proc/self/maps"), 1 << 20);
            StringBuilder sb = new StringBuilder();
            int shown = 0;
            for (String line : maps.split("\n")) {
                int idx = line.indexOf('/');
                if (idx < 0) {
                    continue;
                }
                String path = line.substring(idx).trim();
                if (!path.endsWith(".so")) {
                    continue;
                }
                if (sb.length() > 0) {
                    sb.append(" ;; ");
                }
                sb.append(path);
                if (++shown >= 4) {
                    break;
                }
            }
            return sb.length() == 0 ? "none" : sb.toString();
        });
        put("P07", "maps", "readlink /proc/self/exe", () -> {
            File exe = new File("/proc/self/exe");
            return exe.getCanonicalPath();
        });
        put("P08", "maps", "smaps readable", () -> {
            File smaps = new File("/proc/self/smaps");
            boolean canRead = smaps.canRead();
            String head = canRead ? readText(smaps, 200) : "";
            return "canRead=" + canRead + " headLen=" + head.length();
        });
    }

    // ------------------------------------------------ SO 加载链路（新大类）

    /**
     * 新大类 #3：SO 加载链路，用于验证 B1（虚路径方案）。
     */
    private void probeSoLoad() {
        put("P09", "soload", "findLibrary(probe)", () -> {
            // ClassLoader.findLibrary(String) 是 protected，必须反射调用
            java.lang.reflect.Method m =
                    ClassLoader.class.getDeclaredMethod("findLibrary", String.class);
            m.setAccessible(true);
            Object found = m.invoke(getClass().getClassLoader(), "probe");
            return String.valueOf(found);
        });
        put("P10", "soload", "mapLibraryName", () -> System.mapLibraryName("probe"));
        put("P11", "soload", "nativeLibraryDir exists", () -> {
            String p = ctx.getApplicationInfo().nativeLibraryDir;
            File d = p == null ? null : new File(p);
            String[] list = d == null ? null : d.list();
            return p + " exists=" + (d != null && d.exists())
                    + " files=" + (list == null ? -1 : list.length);
        });
        put("P12", "soload", "System.load(own so by abs path)", () -> {
            // 用 nativeLibraryDir 拼出真实 so 的绝对路径再 load，
            // 验证「绝对路径 dlopen」这条路在容器里是否可用（B1 的前置条件）。
            String dir = ctx.getApplicationInfo().nativeLibraryDir;
            if (dir == null) {
                return "nativeLibraryDir=null";
            }
            File so = new File(dir, "libprobe.so");
            if (!so.exists()) {
                return "libprobe.so missing in " + dir;
            }
            System.load(so.getAbsolutePath());
            return "loaded=" + so.getAbsolutePath();
        });
    }

    // -------------------------------------------- 跨包可见性（新大类）

    /**
     * 新大类 #4：从容器内能看见多少「不该看见」的东西。
     * 这是逃逸面的直接测量，与 literal 类（自己的路径）完全不同。
     */
    private void probeCrossPkg() {
        put("P13", "crosspkg", "host package visible via PM", () -> {
            try {
                ctx.getPackageManager().getPackageInfo("com.nextvm.app", 0);
                return "visible";
            } catch (Throwable t) {
                return "hidden: " + t.getClass().getSimpleName();
            }
        });
        put("P14", "crosspkg", "installedPackages contains host", () -> {
            List<android.content.pm.PackageInfo> all =
                    ctx.getPackageManager().getInstalledPackages(0);
            int hostCount = 0;
            for (android.content.pm.PackageInfo pi : all) {
                if (pi.packageName != null && pi.packageName.contains("nextvm")) {
                    hostCount++;
                }
            }
            return "total=" + all.size() + " hostEntries=" + hostCount;
        });
        put("P15", "crosspkg", "/data/data listable", () -> {
            File d = new File("/data/data");
            String[] names = d.list();
            return "canList=" + (names != null) + " count=" + (names == null ? -1 : names.length);
        });
        put("P16", "crosspkg", "stat host data dir", () -> {
            File host = new File("/data/data/com.nextvm.app");
            return "exists=" + host.exists() + " canRead=" + host.canRead();
        });
    }

    // --------------------------------------- Java 系统属性（新大类）

    /**
     * 新大类 #5：JVM 系统属性。容器会改 Context/文件，但经常忘记改这些，
     * 它们是「一行 println 就能暴露宿主」的典型。
     */
    private void probeSysProps() {
        put("P17", "prop", "java.class.path", () -> System.getProperty("java.class.path"));
        put("P18", "prop", "java.library.path", () -> {
            String v = System.getProperty("java.library.path");
            return v == null ? "null" : (v.length() > 300 ? v.substring(0, 300) + "..." : v);
        });
        put("P19", "prop", "java.vm.name + user.dir", () ->
                "vm=" + System.getProperty("java.vm.name")
                        + " userDir=" + System.getProperty("user.dir"));
    }

    // ------------------------------------------ fd 视图（新大类）

    /**
     * 新大类 #6：/proc/self/fd。打开的 fd 指向的真实路径，
     * 是「路径翻译是否彻底」的照妖镜——翻译只在 open 时刻发生，
     * 已经打开的 fd 会直接暴露物理路径。
     */
    private void probeFd() {
        put("P20", "fd", "fd count", () -> {
            String[] list = new File("/proc/self/fd").list();
            return "count=" + (list == null ? -1 : list.length);
        });
        put("P21", "fd", "fd targets sample", () -> {
            File dir = new File("/proc/self/fd");
            String[] list = dir.list();
            if (list == null) {
                return "cannot list";
            }
            StringBuilder sb = new StringBuilder();
            int shown = 0;
            for (String name : list) {
                try {
                    String target = new File(dir, name).getCanonicalPath();
                    if (sb.length() > 0) {
                        sb.append(" ;; ");
                    }
                    sb.append(name).append("->").append(target);
                    if (++shown >= 5) {
                        break;
                    }
                } catch (Throwable ignore) {
                    // fd 可能在遍历时被关闭
                }
            }
            return sb.toString();
        });
    }

    /**
     * F01-F08：文件系统读写能力。
     *
     * 覆盖容器 path-redirection 必须处理的四类操作：
     *   mkdir（目录创建）/ opendir（目录列举）/ rename（重命名）/ unlink（删除）。
     * 前三条任一缺失 -> 应用"写了却看不到"；unlink 缺失 -> 删到宿主真实文件。
     *
     * 两个根同时测：
     *   sandboxRoot = getExternalFilesDir()            期望落在沙箱
     *   hostRoot    = .../Android/data/<getOpPackageName>/files  期望也被兜底重定向
     */
    private void probeFs() {
        File sandboxRoot = ctx.getExternalFilesDir("fsprobe");
        String opPkg = ctx.getOpPackageName();
        File hostRoot = new File(
                android.os.Environment.getExternalStorageDirectory(),
                "Android/data/" + opPkg + "/files/fsprobe");

        put("F01", "fs", "sandboxRoot path", () ->
                sandboxRoot == null ? "null" : sandboxRoot.getAbsolutePath());
        put("F02", "fs", "hostRoot path", () -> hostRoot.getAbsolutePath());

        put("F03", "fs", "mkdir vs mkdirs", () -> {
            String r1 = sandboxRoot == null ? "skip" : String.valueOf(sandboxRoot.mkdir());
            String r2 = sandboxRoot == null ? "skip" : String.valueOf(sandboxRoot.mkdirs());
            return "mkdir=" + r1 + " mkdirs=" + r2 + " exists="
                    + (sandboxRoot != null && sandboxRoot.isDirectory());
        });

        put("F04", "fs", "write+read", () -> {
            if (sandboxRoot == null) return "skip";
            sandboxRoot.mkdirs();
            File f = new File(sandboxRoot, "a.txt");
            try (java.io.FileOutputStream out = new java.io.FileOutputStream(f)) {
                out.write("hello-fsprobe".getBytes("UTF-8"));
            }
            String back;
            try (java.io.FileInputStream in = new java.io.FileInputStream(f)) {
                byte[] buf = new byte[64];
                int n = in.read(buf);
                back = n <= 0 ? "" : new String(buf, 0, n, "UTF-8");
            }
            return "len=" + f.length() + " content=" + back;
        });

        put("F05", "fs", "listFiles sees it", () -> {
            if (sandboxRoot == null) return "skip";
            String[] names = sandboxRoot.list();
            return "names=" + (names == null ? "null" : java.util.Arrays.toString(names));
        });

        put("F06", "fs", "renameTo", () -> {
            if (sandboxRoot == null) return "skip";
            File a = new File(sandboxRoot, "a.txt");
            File b = new File(sandboxRoot, "b.txt");
            boolean ok = a.renameTo(b);
            String[] names = sandboxRoot.list();
            return "ok=" + ok + " aExists=" + a.exists() + " bExists=" + b.exists()
                    + " names=" + (names == null ? "null" : java.util.Arrays.toString(names));
        });

        put("F07", "fs", "delete", () -> {
            if (sandboxRoot == null) return "skip";
            File b = new File(sandboxRoot, "b.txt");
            boolean ok = b.delete();
            return "deleted=" + ok + " stillExists=" + b.exists();
        });

        put("F08", "fs", "hostRoot mkdir probe", () -> {
            // 走"宿主形态"的路径：若容器兜底，应被重定向到沙箱；若否，会真的建在宿主
            boolean made = hostRoot.exists() || hostRoot.mkdirs();
            String[] names = hostRoot.list();
            return "path=" + hostRoot.getAbsolutePath()
                    + " made=" + made
                    + " list=" + (names == null ? "null" : java.util.Arrays.toString(names));
        });

        // ---- F09-F14：复刻 via 离线列表（ha/e.f + ha/e.e）的完整判断链 ----
        // 真机对照用的证据链：哪一条坏，就对应哪一个 hook 没生效。
        //   F09 写入侧目录        -> getExternalFilesDir("offline") 能否建目录+写文件
        //   F10 六项判断          -> 复刻 ha/e.f 的 canRead/exists/isFile/listFiles/length/lastModified
        //   F11 读取侧宿主形态    -> getOpPackageName() 拼出来的路径能否列到同一批文件
        //   F12 MediaStore 查询   -> 复刻 ha/e.e 的 content://media/external/file
        //   F13 mime/扩展名判定   -> 复刻 ha/e.d 的 mht/pdf 判定
        //   F14 file:// Uri 链路  -> Uri.fromFile -> getPath -> new File -> exists/length

        put("F09", "fs", "offline dir write", () -> {
            try {
                File offline = ctx.getExternalFilesDir("offline");
                if (offline == null) return "getExternalFilesDir(offline)=null";
                boolean made = offline.exists() || offline.mkdirs();
                File mht = new File(offline, "probe-a.mht");
                File txt = new File(offline, "probe-b.txt");
                try (FileOutputStream o1 = new FileOutputStream(mht)) {
                    o1.write("<html><body>probe-a</body></html>".getBytes("UTF-8"));
                }
                try (FileOutputStream o2 = new FileOutputStream(txt)) {
                    o2.write("probe-b".getBytes("UTF-8"));
                }
                return "path=" + offline.getAbsolutePath()
                        + " made=" + made
                        + " mhtLen=" + mht.length()
                        + " txtLen=" + txt.length();
            } catch (Throwable t) {
                return "EX " + t.getClass().getSimpleName() + ": " + t.getMessage();
            }
        });

        put("F10", "fs", "via-filter replica", () -> {
            try {
                File offline = ctx.getExternalFilesDir("offline");
                if (offline == null) return "offline=null";
                // 复刻 ha/e.f 对目录本身的三个判断
                StringBuilder sb = new StringBuilder();
                sb.append("dir.canRead=").append(offline.canRead());
                sb.append(" dir.exists=").append(offline.exists());
                sb.append(" dir.isFile=").append(offline.isFile());
                File[] files = offline.listFiles();
                sb.append(" listFiles=").append(files == null ? "null" : String.valueOf(files.length));
                if (files != null) {
                    for (File f : files) {
                        // 复刻 ha/e.f 循环体里对每个条目的取值
                        sb.append(" [").append(f.getName())
                                .append(" len=").append(f.length())
                                .append(" mtime=").append(f.lastModified())
                                .append(" canRead=").append(f.canRead())
                                .append(" isFile=").append(f.isFile())
                                .append("]");
                    }
                }
                return sb.toString();
            } catch (Throwable t) {
                return "EX " + t.getClass().getSimpleName() + ": " + t.getMessage();
            }
        });

        put("F11", "fs", "host-form list (via read side)", () -> {
            try {
                File hostOffline = new File(
                        Environment.getExternalStorageDirectory(),
                        "Android/data/" + opPkg + "/files/offline");
                String[] names = hostOffline.list();
                File[] files = hostOffline.listFiles();
                StringBuilder sb = new StringBuilder();
                sb.append("opPkg=").append(opPkg);
                sb.append(" path=").append(hostOffline.getAbsolutePath());
                sb.append(" exists=").append(hostOffline.exists());
                sb.append(" list=").append(names == null ? "null" : java.util.Arrays.toString(names));
                if (files != null) {
                    for (File f : files) {
                        sb.append(" [").append(f.getName())
                                .append(" len=").append(f.length()).append("]");
                    }
                }
                return sb.toString();
            } catch (Throwable t) {
                return "EX " + t.getClass().getSimpleName() + ": " + t.getMessage();
            }
        });

        put("F12", "fs", "MediaStore query (via ha/e.e)", () -> {
            android.database.Cursor c = null;
            try {
                android.net.Uri uri = android.provider.MediaStore.Files.getContentUri("external");
                String[] proj = {"_id", "_display_name", "date_added", "mime_type", "_size", "relative_path"};
                c = ctx.getContentResolver().query(
                        uri, proj, "relative_path like ?", new String[]{"%"}, "date_added DESC");
                if (c == null) return "uri=" + uri + " cursor=null";
                StringBuilder sb = new StringBuilder();
                sb.append("uri=").append(uri).append(" count=").append(c.getCount());
                int shown = 0;
                while (c.moveToNext() && shown < 5) {
                    sb.append(" [").append(c.getString(1))
                            .append(" size=").append(c.getLong(4))
                            .append(" mime=").append(c.getString(3))
                            .append(" path=").append(c.getString(5))
                            .append("]");
                    shown++;
                }
                // 另外单独统计「有没有 .mht」
                return sb.toString();
            } catch (Throwable t) {
                return "EX " + t.getClass().getSimpleName() + ": " + t.getMessage();
            } finally {
                if (c != null) try { c.close(); } catch (Throwable ignored) { }
            }
        });

        put("F13", "fs", "mime/ext verdict (via ha/e.d)", () -> {
            try {
                File offline = ctx.getExternalFilesDir("offline");
                if (offline == null) return "offline=null";
                File[] files = offline.listFiles();
                if (files == null) return "listFiles=null";
                StringBuilder sb = new StringBuilder();
                for (File f : files) {
                    String name = f.getName();
                    int dot = name.lastIndexOf('.');
                    String ext = dot < 0 ? null : name.substring(dot + 1).toLowerCase(java.util.Locale.ROOT);
                    String mime = null;
                    try {
                        mime = ctx.getContentResolver().getType(android.net.Uri.fromFile(f));
                    } catch (Throwable ignored) {
                    }
                    boolean isMht = "multipart/related".equals(mime) && "mht".equals(ext);
                    boolean isPdf = "application/pdf".equals(mime) && "pdf".equals(ext);
                    // ha/e.d 的判定：既非 mht 也非 pdf 时返回 null（条目被丢）
                    sb.append(" [").append(name)
                            .append(" ext=").append(ext)
                            .append(" mime=").append(mime)
                            .append(" isMht=").append(isMht)
                            .append(" isPdf=").append(isPdf)
                            .append(" keep=").append(isMht || isPdf || mime == null)
                            .append("]");
                }
                return sb.toString();
            } catch (Throwable t) {
                return "EX " + t.getClass().getSimpleName() + ": " + t.getMessage();
            }
        });

        put("F14", "fs", "file uri chain (via ha/e.f)", () -> {
            try {
                File offline = ctx.getExternalFilesDir("offline");
                if (offline == null) return "offline=null";
                File[] files = offline.listFiles();
                if (files == null || files.length == 0) return "no entries";
                StringBuilder sb = new StringBuilder();
                for (File f : files) {
                    android.net.Uri u = android.net.Uri.fromFile(f);
                    String p = u.getPath();
                    File back = p == null ? null : new File(p);
                    sb.append(" [uri=").append(u.getScheme()).append("://")
                            .append(" path=").append(p)
                            .append(" exists=").append(back != null && back.exists())
                            .append(" len=").append(back == null ? -1 : back.length())
                            .append("]");
                }
                return sb.toString();
            } catch (Throwable t) {
                return "EX " + t.getClass().getSimpleName() + ": " + t.getMessage();
            }
        });
    }

    // ---------------------------------------- 挂载表（新大类）

    /**
     * 新大类 #7：/proc/self/mountinfo。容器若用 mount namespace 伪装路径，
     * 这里会留下痕迹；反之如果有不该出现在沙箱里的挂载点，也在这里暴露。
     */
    private void probeMount() {
        put("P22", "mount", "mountinfo readable", () -> {
            File f = new File("/proc/self/mountinfo");
            boolean canRead = f.canRead();
            int lines = 0;
            if (canRead) {
                String s = readText(f, 1 << 20);
                for (String l : s.split("\n")) {
                    if (!l.isEmpty()) {
                        lines++;
                    }
                }
            }
            return "canRead=" + canRead + " lines=" + lines;
        });
        put("P23", "mount", "sandbox mount points", () -> {
            File f = new File("/proc/self/mountinfo");
            if (!f.canRead()) {
                return "unreadable";
            }
            String s = readText(f, 1 << 20);
            int sandbox = 0;
            StringBuilder sb = new StringBuilder();
            for (String l : s.split("\n")) {
                if (l.contains("com.nextvm.app") || l.contains("/virtual/")) {
                    sandbox++;
                    if (sb.length() < 200) {
                        int idx = l.indexOf(" - ");
                        sb.append(idx > 0 ? l.substring(idx) : l).append(" ;; ");
                    }
                }
            }
            return "sandboxMounts=" + sandbox + " " + sb;
        });
    }

    /**
     * 合并 UiProbe 在 UI 线程采到的结果（PopupWindow / 子窗口，uiprobe 大类 P24-P27）。
     * 必须在 ProbeActivity 里先跑过 UiProbe.run()，否则这里是空的。
     */
    private void probeUiEntries() {
        org.json.JSONArray ui;
        try {
            ui = UiProbe.takeEntries();
        } catch (Throwable t) {
            put("P24", "uiprobe", "takeEntries", () -> {
                throw new IllegalStateException("UiProbe.takeEntries failed: " + t);
            });
            return;
        }
        if (ui == null) {
            return;
        }
        synchronized (entries) {
            for (int i = 0; i < ui.length(); i++) {
                try {
                    entries.put(ui.getJSONObject(i));
                } catch (Throwable ignored) {
                    // 单条失败不影响其余
                }
            }
        }
    }

    // ------------------------------------------------------------------ native

    private void probeNative() {
        String dataDir = "/data/data/" + pkg;
        try {
            String out = NativeProbe.runAll(dataDir, pkg);
            if (out == null) {
                put("N00", "native", "runAll", () -> {
                    throw new IllegalStateException("native returned null");
                });
                return;
            }
            for (String line : out.split("\n")) {
                if (line.isEmpty()) {
                    continue;
                }
                String[] parts = line.split("\t", 3);
                if (parts.length < 3) {
                    continue;
                }
                final String id = parts[0];
                final String status = parts[1];
                final String value = parts[2];
                JSONObject o = new JSONObject();
                o.put("id", id);
                o.put("group", "native");
                o.put("name", id);
                o.put("value", value);
                o.put("status", "ok".equals(status) ? "ok" : "fail");
                synchronized (entries) {
                    entries.put(o);
                }
            }
        } catch (Throwable t) {
            put("N00", "native", "runAll", () -> {
                throw t instanceof Exception ? (Exception) t : new RuntimeException(t);
            });
        }
    }

    // ------------------------------------------------------------------ utils

    private void put(String id, String group, String name, Report.Probe probe) {
        // 套件过滤：本轮只跑重点（默认 round = 路径/写入/诊断）；其余记 skipped，代码保留
        if (!suite.enabled(id)) {
            putSkipped(id, group, name);
            return;
        }
        Report.put(entries, id, group, name, probe);
    }

    private void putSkipped(String id, String group, String name) {
        try {
            JSONObject o = new JSONObject();
            o.put("id", id);
            o.put("group", group);
            o.put("name", name);
            o.put("status", "skipped");
            entries.put(o);
        } catch (Throwable ignored) {
            // 记录 skipped 失败不影响其它项
        }
    }

    private static String readText(File f, int max) throws Exception {
        // 必须循环读：单次 read() 对 /proc/self/maps 这类大文件会截断
        // （实测只读到 32 行，实际几百行），会让 P05/P06 的统计完全失真。
        java.io.ByteArrayOutputStream bos = new java.io.ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        try (FileInputStream fis = new FileInputStream(f)) {
            int n;
            while (bos.size() < max && (n = fis.read(buf)) > 0) {
                bos.write(buf, 0, Math.min(n, max - bos.size()));
            }
        }
        byte[] all = bos.toByteArray();
        return new String(all, 0, all.length, "UTF-8").trim();
    }

    /** 自动判断当前是「直接安装」还是「容器内」。 */
    private String detectMode() {
        try {
            int my = Process.myUid();
            int official = ctx.getApplicationInfo().uid;
            if (my != official) {
                return "container:uid-mismatch(" + my + "!=" + official + ")";
            }
            String dd = ctx.getDataDir().getAbsolutePath();
            if (!dd.startsWith("/data/user/0/" + pkg) && !dd.startsWith("/data/data/" + pkg)) {
                return "container:dir-other(" + dd + ")";
            }
            return "installed";
        } catch (Throwable t) {
            return "unknown:" + t;
        }
    }
}
