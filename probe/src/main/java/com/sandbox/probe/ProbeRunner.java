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
        probeNative();

        JSONObject root = new JSONObject();
        root.put("schema", 1);
        root.put("mode", detectMode());
        root.put("packageName", pkg);
        root.put("timeline", Report.timeline());
        root.put("provider", Report.providerEntries());
        root.put("entries", entries);
        return root;
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
        Report.put(entries, id, group, name, probe);
    }

    private static String readText(File f, int max) throws Exception {
        byte[] buf = new byte[max];
        int n;
        try (FileInputStream fis = new FileInputStream(f)) {
            n = fis.read(buf);
        }
        return new String(buf, 0, Math.max(n, 0), "UTF-8").trim();
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
