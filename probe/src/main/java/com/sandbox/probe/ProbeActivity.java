package com.sandbox.probe;

import android.app.Activity;
import android.os.Bundle;
import android.util.Log;

import org.json.JSONObject;

/**
 * 触发全量探针。
 *
 * 重要设计（T2-pre 之后修正）：
 *   **不创建任何 View、不调用 setContentView。**
 *   在应用级虚拟化容器里，guest 的 Resources 往往没有正确绑定
 *   （ShitJumpingVM 上表现为 breadcrumb "getResources()=THREW"），
 *   一旦 new TextView(this) 就会抛 Resources$NotFoundException，
 *   整个探针在进入 ProbeRunner 之前就死了。
 *   探针的目的是测平台 API，不需要 UI，因此彻底去掉渲染依赖。
 */
public class ProbeActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Report.mark("Activity.onCreate");

        // UI 层探针（uiprobe 大类）：PopupWindow / 子窗口必须在 UI 线程、Activity 存活时测。
        // 结果暂存在 UiProbe，由 ProbeRunner 合并进报告。
        try {
            UiProbe.run(this);
        } catch (Throwable t) {
            Log.w("PROBE", "UiProbe failed: " + t);
        }

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
