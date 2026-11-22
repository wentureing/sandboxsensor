package com.sandbox.probe;

/** JNI 入口；具体实现在 src/main/cpp/native_probe.cpp。 */
public final class NativeProbe {

    static {
        System.loadLibrary("probe");
    }

    private NativeProbe() {
    }

    /**
     * @param dataDir 字面量目录，如 /data/data/com.sandbox.probe
     * @param pkg     包名
     * @return 多行文本：id \t ok|fail \t value
     */
    public static native String runAll(String dataDir, String pkg);
}
