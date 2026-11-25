package com.sandbox.probe;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/**
 * 探针套件：**只跑本轮重点**，其余暂停（结果记 skipped，代码不删）。
 *
 * 选择方式（探针不自动执行，必须先显式启动）：
 *   adb shell setprop debug.probe.suite round|path|diag|all|none
 *   （缺省 round：路径族 J* + 文件系统写入 F* + 诊断通道 D*）
 *
 * 不依赖 core:common（探针是独立 APK），系统属性用反射读取，读不到就用 round。
 */
public final class ProbeSuite {

    public static final String PROP = "debug.probe.suite";

    /** 本轮重点：路径（J）、写入（F）、诊断通道（D）。P/N 系列暂停。 */
    private static final Set<String> ROUND_PREFIXES =
            new HashSet<>(Arrays.asList("J", "F", "D"));

    /** null 表示"全选"。 */
    private final Set<String> ids;
    private final String name;

    private ProbeSuite(String name, Set<String> ids) {
        this.name = name;
        this.ids = ids;
    }

    public static ProbeSuite current() {
        String v = readProp();
        switch (v) {
            case "all":
                return new ProbeSuite("all", null);
            case "none":
                return new ProbeSuite("none", new HashSet<>());
            case "path":
                return new ProbeSuite("path", new HashSet<>(Arrays.asList("J", "F")));
            case "diag":
                return new ProbeSuite("diag", new HashSet<>(Arrays.asList("D")));
            default:
                return new ProbeSuite("round", new HashSet<>(ROUND_PREFIXES));
        }
    }

    public String name() {
        return name;
    }

    /** id 形如 "J02"；先按完整 id 匹配，再按大类首字母匹配。 */
    public boolean enabled(String id) {
        if (ids == null) return true;
        if (id == null || id.isEmpty()) return false;
        if (ids.contains(id)) return true;
        return ids.contains(id.substring(0, 1));
    }

    private static String readProp() {
        try {
            Class<?> c = Class.forName("android.os.SystemProperties");
            Object v = c.getMethod("get", String.class, String.class)
                    .invoke(null, PROP, "round");
            return v == null ? "round" : v.toString().trim().toLowerCase();
        } catch (Throwable t) {
            return "round";
        }
    }
}
