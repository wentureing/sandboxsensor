package com.sandbox.probe;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.util.Log;

import org.json.JSONObject;

/**
 * 探针主入口（v1.1 起）。
 *
 * 为什么改用 ContentProvider 触发：
 *   在应用级虚拟化容器里，guest 的 Resources 往往未正确绑定
 *   （ShitJumpingVM breadcrumb: getResources()=THREW），而 Activity 启动时
 *   框架会在 super.onCreate() 内调用 PhoneWindow.setDefaultIcon() →
 *   Context.getDrawable(activityInfo.icon)，其中 icon 仍是宿主 stub 的资源 ID
 *   → Resources$NotFoundException，任何 Activity 都无法启动。
 *
 *   而 Provider 阶段容器已实测可用（provider ok → Application.onCreate），
 *   因此把探针触发放在这里：导入 APK 后只要容器启动 guest，探针就会自动跑完，
 *   完全不需要点开任何界面、不触碰 Window / Resources。
 */
public class ProbeProvider extends ContentProvider {

    private static final String TAG = "PROBE";

    @Override
    public boolean onCreate() {
        Report.mark("ContentProvider.onCreate");
        final Context ctx = getContext();
        if (ctx == null) {
            Report.mark("ContentProvider.onCreate:nullContext");
            return true;
        }

        // 1) 同步快照 + 立即落盘：即使随后任何阶段崩溃，也有 Provider 阶段证据
        Report.providerSnapshot(ctx);
        Report.flushEarly(ctx, "ContentProvider.onCreate");

        // 2) 后台线程跑全量探针：不依赖 Activity
        new Thread(() -> {
            try {
                JSONObject payload = new ProbeRunner(ctx).run();
                Report.flush(ctx, payload);
            } catch (Throwable t) {
                Log.e(TAG, "fatal in ProbeRunner(provider entry)", t);
            }
        }, "probe-runner-provider").start();

        return true;
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs,
                        String sortOrder) {
        return null;
    }

    @Override
    public String getType(Uri uri) {
        return null;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public Bundle call(String method, String arg, Bundle extras) {
        return null;
    }
}
