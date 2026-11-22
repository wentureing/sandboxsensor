package com.sandbox.probe;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;

/**
 * 最早能拿到 Context 的位置（早于 Application.onCreate）。
 *
 * 这里打两个点：
 *   1) 时序标记 → 用于验证容器是否在 Provider 之前完成 Context 替换；
 *   2) 路径快照 → 若容器接管太晚，这里读到的就是宿主真实路径，会被记录下来。
 */
public class ProbeProvider extends ContentProvider {

    @Override
    public boolean onCreate() {
        Report.mark("ContentProvider.onCreate");
        Context ctx = getContext();
        if (ctx != null) {
            Report.providerSnapshot(ctx);
            // 容器里 Activity 可能在资源绑定时崩溃（见 T2-pre 记录），
            // 这里先把 Provider 阶段的证据落盘，保证即使后续崩溃也有数据可查。
            Report.flushEarly(ctx, "ContentProvider.onCreate");
        } else {
            Report.mark("ContentProvider.onCreate:nullContext");
        }
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
