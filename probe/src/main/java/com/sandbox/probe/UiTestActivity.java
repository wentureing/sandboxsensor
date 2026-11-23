package com.sandbox.probe;

import android.app.Activity;
import android.os.Bundle;
import android.util.Log;
import android.view.Gravity;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * UiTestActivity（v1.5）：专门用于「文字选择 / popup」排查的纯 UI 页面。
 *
 * 设计约束（按要求）：
 *   * 只显示文本，**不执行任何虚拟环境检查**（不跑 ProbeRunner、不读 /proc、不写报告）
 *   * 文本必须可长按选中（setTextIsSelectable），以便复现 FloatingActionMode
 *   * **不自动 finish**：等待外部脚本操作，或者被系统/容器杀掉
 *
 * 排查目的：
 *   NEXTVM 里长按文字 -> 出现选中区域但看不到「复制/全选」菜单 -> 几秒后 ANR 被杀。
 *   WMS 层已确认 PopupWindow(type=1002) 窗口存在且 VISIBLE，但屏幕上没有内容，
 *   怀疑菜单为空壳（容器 Window.Callback 未转发 onCreateActionMode）。
 *   有了这个纯净页面，就能排除 Via/WebView 的干扰，直接验证 TextView 的选择菜单。
 */
public class UiTestActivity extends Activity {

    private static final String TAG = "PROBE";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Log.i(TAG, "UiTestActivity.onCreate enter");

        // 每一步都单独 try/catch：容器内 Resources 可能未绑定，
        // 任何一步失败都要把异常写进 logcat，方便定位。
        try {
            LinearLayout root = new LinearLayout(this);
            root.setOrientation(LinearLayout.VERTICAL);
            root.setPadding(40, 120, 40, 40);

            TextView title = new TextView(this);
            title.setText("UiTestActivity — 长按下面的文字");
            title.setTextSize(22);
            root.addView(title);

            TextView body = new TextView(this);
            body.setText(
                    "第一段：这是一段专门用于测试文字选择菜单的文本。\n\n"
                    + "第二段：长按这里应该出现「复制 / 全选 / 分享」等浮动菜单。\n\n"
                    + "第三段：如果只见选中高亮、看不到菜单，说明 FloatingActionMode 的 popup 是空壳。\n\n"
                    + "第四段：本页面不做任何虚拟环境检查，也不会自动关闭，"
                    + "等待脚本操作或进程被杀。");
            body.setTextSize(18);
            body.setTextIsSelectable(true);   // 关键：允许长按选中
            body.setGravity(Gravity.START);

            ScrollView scroll = new ScrollView(this);
            scroll.addView(body);
            root.addView(scroll, new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

            setContentView(root);
            Log.i(TAG, "UiTestActivity UI ready (selectable=" + body.isTextSelectable()
                    + " viewId=" + body.getId() + ")");
        } catch (Throwable t) {
            // 容器内 Resources 未绑定时，new TextView 会抛 Resources$NotFoundException。
            // 这里把异常完整打出来，这本身就是一个关键结论。
            Log.e(TAG, "UiTestActivity UI build FAILED: " + t, t);
            try {
                // 退化为最裸的内容：直接 setContentView 一个空 View，
                // 至少让 Activity 留在前台，方便对比。
                setContentView(new android.view.View(this));
            } catch (Throwable t2) {
                Log.e(TAG, "UiTestActivity fallback view FAILED: " + t2, t2);
            }
        }

        // 故意不 finish：等待脚本操作或被系统杀掉。
        Log.i(TAG, "UiTestActivity.onCreate leave (will NOT auto-finish)");

        // v1.7：本页同时承担「探针执行入口」。
        // v1.6 把 LAUNCHER 移到本页后，ProbeActivity 不再被容器拉起，
        // 导致 J01/P09 等数据拿不到（实测 PROBE 分块 = 0）。
        // 这里后台跑一次 ProbeRunner，报告仍走 logcat(tag=PROBE) + 文件双通道。
        new Thread(() -> {
            JSONObject payload;
            try {
                payload = new ProbeRunner(this).run();
            } catch (Throwable t) {
                Log.e(TAG, "ProbeRunner fatal: " + t, t);
                payload = new JSONObject();
                Report.putFatal(payload, t);
            }
            Report.flush(this, payload);
        }, "probe-runner").start();
    }

    @Override
    protected void onDestroy() {
        Log.i(TAG, "UiTestActivity.onDestroy");
        super.onDestroy();
    }
}
