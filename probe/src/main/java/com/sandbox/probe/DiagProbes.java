package com.sandbox.probe;

import android.content.Context;

import java.io.File;

/**
 * D01–D05：诊断通道是否按预期产出（存在 / 行数 / 尾部 / 总量 / 可写）。
 *
 * 说明：docs/代码镜像/probe/DiagProbes.md
 */
final class DiagProbes {

    /** watchdog 的 stackdump 在 bind 后 +0.3s…+20s 分批写入；超过该阈值仍无才算缺失。 */
    private static final long STACKDUMP_WAIT_MS = 25_000L;

    private DiagProbes() {
    }

    static void run(Context ctx, ProbeSink sink, long startedElapsedMs) {
        final File files = ctx.getFilesDir();
        final int pid = android.os.Process.myPid();

        sink.put("D01", "diag", "breadcrumb.exists", () -> {
            File f = new File(files, "breadcrumb-" + pid + ".log");
            return "exists=" + f.exists() + " size=" + (f.exists() ? f.length() : 0L);
        });
        sink.put("D02", "diag", "checks.lines", () -> {
            File f = new File(files, "checks.log");
            if (!f.exists()) return "exists=false";
            String s = ProbeRunner.readText(f, 64 * 1024);
            int lines = s.isEmpty() ? 0 : s.split("\n", -1).length;
            return "exists=true lines=" + lines;
        });
        sink.put("D03", "diag", "stackdump.exists", () -> {
            File f = new File(files, "stackdump-" + pid + ".log");
            if (f.exists()) return "exists=true size=" + f.length();
            // 三态：报告可能早于 watchdog 的转储点，那时不是"缺失"
            long age = android.os.SystemClock.elapsedRealtime() - startedElapsedMs;
            String state = age < STACKDUMP_WAIT_MS ? "not-yet" : "missing";
            return "exists=false state=" + state + " ageMs=" + age;
        });
        sink.put("D04", "diag", "log.total", () -> {
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
        sink.put("D05", "diag", "checks.tail", () -> {
            File f = new File(files, "checks.log");
            if (!f.exists()) return "exists=false";
            String s = ProbeRunner.readText(f, 64 * 1024);
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
}
