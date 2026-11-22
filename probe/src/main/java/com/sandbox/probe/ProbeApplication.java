package com.sandbox.probe;

import android.app.Application;
import android.content.Context;

/**
 * 记录最早的启动时序点。
 *
 * 目的：验证「承接必须在 Provider 之前完成」这一结论（见 07 文档 §2）：
 *   attachBaseContext → ContentProvider.onCreate → Application.onCreate
 * 注意顺序不是直觉上的 Application 先于 Provider。
 */
public class ProbeApplication extends Application {

    @Override
    protected void attachBaseContext(Context base) {
        super.attachBaseContext(base);
        Report.mark("Application.attachBaseContext");
    }

    @Override
    public void onCreate() {
        super.onCreate();
        Report.mark("Application.onCreate");
    }
}
