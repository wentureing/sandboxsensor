package com.sandbox.probe;

import android.content.Context;
import android.graphics.PixelFormat;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.PopupWindow;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * UI 层探针（新大类 uiprobe）。
 *
 * 为什么单独有这个类：
 *   ProbeRunner 跑在后台线程，而 PopupWindow / WindowManager.addView 必须在 UI 线程，
 *   且需要一个 Activity 作为 anchor。所以由 ProbeActivity 在 onCreate（UI 线程）
 *   同步调用本类，结果通过 UiProbe.takeEntries() 交给 ProbeRunner 合并。
 *
 * 检测目的（对应 NEXTVM 的「长按文字选择框卡死」）：
 *   长按文本 -> FloatingActionMode -> FloatingToolbar 是一个 PopupWindow
 *   如果 PopupWindow / 子窗口在当前容器里无法正常显示，就会卡在主线程 -> ANR。
 *   这里把这条链路拆成 3 步分别验证，不用真的长按。
 *
 * 安全策略：
 *   容器内 Resources 可能未正确绑定（new TextView 会抛 Resources$NotFoundException），
 *   所以这里只用最裸的 View(Context)，并且每一步都独立 try/catch，
 *   失败也能拿到异常类型与消息。
 */
public final class UiProbe {

    private static JSONArray entries;

    private UiProbe() {
    }

    /** 由 ProbeActivity 在 UI 线程调用。 */
    public static synchronized void run(android.app.Activity activity) {
        JSONArray out = new JSONArray();
        if (activity == null) {
            putRaw(out, "P24", "activity", "activity==null");
            entries = out;
            return;
        }

        // P24：裸 View 能否创建（容器 Resources 是否可用）
        try {
            View v = new View(activity);
            putRaw(out, "P24", "new View(activity)", "ok " + v.getClass().getName());
        } catch (Throwable t) {
            putRaw(out, "P24", "new View(activity)", "THREW " + t);
        }

        // P25：PopupWindow 能否真正 show 出来（FloatingToolbar 的机制）
        try {
            PopupWindow pw = new PopupWindow(activity);
            View content = new View(activity);
            pw.setContentView(content);
            pw.setWidth(200);
            pw.setHeight(100);
            pw.setFocusable(false);
            View anchor = activity.getWindow().getDecorView();
            pw.showAtLocation(anchor, Gravity.CENTER, 0, 0);
            boolean showing = pw.isShowing();
            try {
                pw.dismiss();
            } catch (Throwable ignored) {
            }
            putRaw(out, "P25", "PopupWindow.showAtLocation", "showing=" + showing);
        } catch (Throwable t) {
            putRaw(out, "P25", "PopupWindow.showAtLocation", "THREW " + t);
        }

        // P26：直接往 WindowManager 加 TYPE_APPLICATION_ATTACHED_DIALOG 子窗口
        //      （FloatingActionMode 用的就是这一类）
        try {
            WindowManager wm = (WindowManager) activity.getSystemService(Context.WINDOW_SERVICE);
            View v = new View(activity);
            WindowManager.LayoutParams lp = new WindowManager.LayoutParams(
                    200, 100,
                    WindowManager.LayoutParams.TYPE_APPLICATION_ATTACHED_DIALOG,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                            | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
                    PixelFormat.TRANSLUCENT);
            lp.gravity = Gravity.CENTER;
            lp.token = activity.getWindow().getDecorView().getWindowToken();
            wm.addView(v, lp);
            wm.removeView(v);
            putRaw(out, "P26", "addView(ATTACHED_DIALOG)",
                    "added+removed ok token=" + (lp.token != null));
        } catch (Throwable t) {
            putRaw(out, "P26", "addView(ATTACHED_DIALOG)", "THREW " + t);
        }

        // P27：Activity 的 Window/DecorView 基本信息（判 anchor 是否可用）
        try {
            View decor = activity.getWindow() == null ? null : activity.getWindow().getDecorView();
            putRaw(out, "P27", "window/decorView",
                    "decor=" + (decor == null ? "null" : decor.getClass().getSimpleName())
                            + " token=" + (decor != null && decor.getWindowToken() != null)
                            + " attached=" + (decor != null && decor.isAttachedToWindow()));
        } catch (Throwable t) {
            putRaw(out, "P27", "window/decorView", "THREW " + t);
        }

        entries = out;
    }

    /** ProbeRunner 取结果（取完即清，避免重复合并）。 */
    public static synchronized JSONArray takeEntries() {
        JSONArray e = entries;
        entries = null;
        return e == null ? new JSONArray() : e;
    }

    private static void putRaw(JSONArray out, String id, String name, String value) {
        try {
            JSONObject o = new JSONObject();
            o.put("id", id);
            o.put("group", "uiprobe");
            o.put("name", name);
            o.put("value", value);
            o.put("status", value.startsWith("THREW") ? "fail" : "ok");
            out.put(o);
        } catch (Throwable ignored) {
        }
    }
}
