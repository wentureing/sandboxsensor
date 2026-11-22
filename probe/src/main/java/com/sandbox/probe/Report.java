package com.sandbox.probe;

import android.content.Context;
import android.os.Process;
import android.os.SystemClock;
import android.util.Base64;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

/**
 * 报告收集与输出。
 *
 * 输出通道（重要设计）：
 *   1) logcat，tag=PROBE，分块 base64 —— 主通道。不依赖包是否安装、不依赖 run-as，
 *      因此「卸载后再作为 guest 导入容器」的场景也能采集到报告。
 *   2) files/probe-report.json —— 备份通道，便于 run-as 拉取。
 */
public final class Report {

    public static final String TAG = "PROBE";
    private static final JSONArray TIMELINE = new JSONArray();
    private static final JSONArray PROVIDER = new JSONArray();

    private Report() {
    }

    /** 记录一个启动时序点。 */
    public static synchronized void mark(String phase) {
        try {
            JSONObject o = new JSONObject();
            o.put("phase", phase);
            o.put("uptimeMs", SystemClock.uptimeMillis());
            o.put("wallMs", System.currentTimeMillis());
            o.put("pid", Process.myPid());
            o.put("process", currentProcessName());
            TIMELINE.put(o);
            Log.i(TAG, "PHASE " + phase + " uptime=" + SystemClock.uptimeMillis()
                    + " pid=" + Process.myPid() + " process=" + currentProcessName());
        } catch (Throwable t) {
            Log.w(TAG, "mark failed: " + t);
        }
    }

    /** ContentProvider 阶段能看到的路径快照（用于验证承接时机）。 */
    public static synchronized void providerSnapshot(Context ctx) {
        put(PROVIDER, "P01.provider.getDataDir", "provider", "getDataDir",
                () -> String.valueOf(ctx.getDataDir()));
        put(PROVIDER, "P02.provider.getFilesDir", "provider", "getFilesDir",
                () -> String.valueOf(ctx.getFilesDir()));
        put(PROVIDER, "P03.provider.filesDirExists", "provider", "filesDir.exists",
                () -> String.valueOf(ctx.getFilesDir().exists()));
        put(PROVIDER, "P04.provider.packageName", "provider", "getPackageName",
                ctx::getPackageName);
        put(PROVIDER, "P05.provider.myUid", "provider", "Process.myUid",
                () -> String.valueOf(Process.myUid()));
        put(PROVIDER, "P06.provider.sourceDir", "provider", "applicationInfo.sourceDir",
                () -> String.valueOf(ctx.getApplicationInfo().sourceDir));
    }

    /** 供 ProbeRunner 复用的小工具：把一条结果安全地塞进数组。 */
    static void put(JSONArray target, String id, String group, String name, Probe probe) {
        JSONObject o = new JSONObject();
        try {
            o.put("id", id);
            o.put("group", group);
            o.put("name", name);
            o.put("value", probe.get());
            o.put("status", "ok");
        } catch (Throwable t) {
            try {
                o.put("status", "fail");
                o.put("error", t.getClass().getSimpleName() + (t.getMessage() == null ? "" : (": " + t.getMessage())));
            } catch (Throwable ignored) {
                // org.json 极少失败，忽略
            }
            Log.w(TAG, "probe fail " + id + " -> " + t);
        }
        synchronized (target) {
            target.put(o);
        }
    }

    @FunctionalInterface
    interface Probe {
        String get() throws Exception;
    }

    public static JSONArray timeline() {
        return TIMELINE;
    }

    public static JSONArray providerEntries() {
        return PROVIDER;
    }

    public static String currentProcessName() {
        try {
            return android.app.Application.getProcessName();
        } catch (Throwable t) {
            return "unknown";
        }
    }

    /** 输出报告：文件 + 分块 base64 logcat。 */
    public static void flush(Context ctx, JSONObject payload) {
        String json = payload.toString();

        try {
            File f = new File(ctx.getFilesDir(), "probe-report.json");
            try (FileOutputStream fos = new FileOutputStream(f)) {
                fos.write(json.getBytes(StandardCharsets.UTF_8));
            }
            Log.i(TAG, "FILE " + f.getAbsolutePath() + " bytes=" + json.length());
        } catch (Throwable t) {
            Log.w(TAG, "file write failed: " + t);
        }

        try {
            String b64 = Base64.encodeToString(json.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
            final int chunk = 1800;
            int total = (b64.length() + chunk - 1) / chunk;
            for (int i = 0; i < total; i++) {
                int start = i * chunk;
                int end = Math.min(b64.length(), start + chunk);
                Log.i(TAG, "REPORT v1 part " + (i + 1) + "/" + total + " " + b64.substring(start, end));
            }
            Log.i(TAG, "REPORT v1 end parts=" + total + " jsonBytes=" + json.getBytes(StandardCharsets.UTF_8).length);
        } catch (Throwable t) {
            Log.e(TAG, "chunk output failed: " + t);
        }
    }

    public static void putFatal(JSONObject payload, Throwable t) {
        try {
            payload.put("fatal", t.getClass().getName() + ": " + t.getMessage());
        } catch (Throwable ignored) {
        }
    }

    /**
     * 早期落盘：把当前 timeline + provider 快照写到 files/probe-early.json。
     * 用于「Activity 阶段崩溃、但 Provider 已跑完」的场景（见 records/T2-pre-*.md）。
     */
    public static void flushEarly(Context ctx, String stage) {
        try {
            JSONObject o = new JSONObject();
            o.put("schema", 1);
            o.put("stage", stage);
            o.put("packageName", ctx.getPackageName());
            o.put("timeline", TIMELINE);
            o.put("provider", PROVIDER);
            String json = o.toString();

            File f = new File(ctx.getFilesDir(), "probe-early.json");
            try (FileOutputStream fos = new FileOutputStream(f)) {
                fos.write(json.getBytes(StandardCharsets.UTF_8));
            }
            // 同时走 logcat：容器内 files 目录在宿主机不易定位，logcat 更容易捞
            String b64 = Base64.encodeToString(json.getBytes(StandardCharsets.UTF_8), Base64.NO_WRAP);
            final int chunk = 1800;
            int total = (b64.length() + chunk - 1) / chunk;
            for (int i = 0; i < total; i++) {
                int s = i * chunk;
                int e = Math.min(b64.length(), s + chunk);
                Log.i(TAG, "EARLY v1 part " + (i + 1) + "/" + total + " " + b64.substring(s, e));
            }
            Log.i(TAG, "EARLY v1 end stage=" + stage + " parts=" + total);
        } catch (Throwable t) {
            Log.w(TAG, "flushEarly failed: " + t);
        }
    }
}
