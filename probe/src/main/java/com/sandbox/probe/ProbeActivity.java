package com.sandbox.probe;

import android.app.Activity;
import android.os.Bundle;
import android.util.Log;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * 触发全量探针。
 *
 * 设计要点：
 *   * 探针在后台线程执行，避免 ANR；
 *   * 结果同时输出到 logcat（分块 base64，主通道）与 files/probe-report.json（备份）；
 *   * 跑完自己 finish()，便于 adb 自动化；容器内 finish 行为异常也不影响已输出的报告。
 */
public class ProbeActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Report.mark("Activity.onCreate");

        TextView tv = new TextView(this);
        tv.setText("Sandbox Probe\nrunning…");
        tv.setPadding(48, 96, 48, 48);
        setContentView(tv);

        new Thread(() -> {
            JSONObject payload;
            try {
                payload = new ProbeRunner(this).run();
            } catch (Throwable t) {
                Log.e("PROBE", "fatal in ProbeRunner", t);
                payload = new JSONObject();
                Report.putFatal(payload, t);
            }
            Report.flush(this, payload);
            runOnUiThread(() -> {
                try {
                    finish();
                } catch (Throwable ignored) {
                    // 容器内可能不允许正常 finish，忽略
                }
            });
        }, "probe-runner").start();
    }
}
