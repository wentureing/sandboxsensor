package com.sandbox.probe;

import android.app.Activity;
import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.res.AssetManager;
import android.content.res.Resources;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.ViewGroup;
import android.webkit.MimeTypeMap;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.lang.reflect.Method;
import java.net.URLConnection;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;

/**
 * WebViewProbeActivity v1.10 —— 用实践证明 WebView 在容器里的行为。
 *
 * v1.9 的三个 bug（本次修复）：
 *   1. W0 在后台线程 new WebView() -> "All WebView methods must be called on the same thread"
 *      -> v1.10 起所有 WebView 操作都在主线程，用 Handler 串联而非后台线程
 *   2. W4 copy 自己覆盖自己（getFilesDir() 和 getDataDir()/files 在容器里指向同一路径）-> len=0
 *      -> v1.10 用不同的目标文件名
 *   3. W3 只拿到 ERR_FILE_NOT_FOUND，不知道 android_asset 到底解析到哪
 *      -> v1.10 在 shouldInterceptRequest 里打印实际 URL，并加 W11 主动返回 text/html 做对照
 *
 * 实验矩阵（v1.10）：
 *   W8   webView.getContext() 是谁（packageName / class / identityHash）
 *   W12  webView.getContext().getAssets() 是否等于 this.getAssets()
 *   W9   MimeTypeMap.getSingleton().getMimeTypeFromExtension("html")
 *   W10  URLConnection.guessContentTypeFromName("x.html")
 *   W0   new WebView() + getSettings()（主线程）
 *   W5   ApplicationInfo.getAllApkPaths()
 *   W6   宿主 getAssets() 里有没有 WebView 资源（list("webkit/") 的实际内容）
 *   W7   getResources().getInteger(0x20c0006)
 *   W11  shouldInterceptRequest 打印 file:// 请求，并主动返回 text/html
 *   W1   loadDataWithBaseURL(about:blank)                <- 对照：渲染是否正常
 *   W2   loadUrl(file://<filesDir>/wvprobe.html)         <- 核心：是否显示源码
 *   W3   loadUrl(file:///android_asset/wvprobe.html)
 *   W4   loadUrl(file://<dataDir>/files/wvprobe_w4.html) <- 用不同文件名
 *
 * 触发：
 *   adb shell am broadcast -n com.nextvm.app/.CmdReceiver -a com.nextvm.cmd.LAUNCH_ACTIVITY \
 *       --es pkg com.sandbox.probe --es activity com.sandbox.probe.WebViewProbeActivity
 *
 * 结果：
 *   logcat -s WVP
 *   getFilesDir()/wvprobe-result.txt
 */
public class WebViewProbeActivity extends Activity {

    private static final String TAG = "WVP";
    private static final String HTML_NAME = "wvprobe.html";
    private static final String HTML_W4 = "wvprobe_w4.html";
    private static final String HTML_BODY =
            "<html><body><h1 id='h'>WVPAGE_OK</h1><p>hello from probe</p></body></html>";

    private final List<String> lines = new ArrayList<>();
    private FileOutputStream out;
    private final Handler main = new Handler(Looper.getMainLooper());

    private WebView wv;
    private TextView status;
    private int w11InterceptCount = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        status = new TextView(this);
        status.setText("WebViewProbe v1.10 running...");
        root.addView(status, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(root);

        // 整个流程都在主线程（WebView 必须在有 Looper 的线程）
        main.post(() -> {
            try {
                runAll(root);
            } catch (Throwable t) {
                rec("FATAL in runAll: " + t);
                Log.e(TAG, "runAll failed", t);
            }
        });
    }

    // ---------------------------------------------------------------- 记录

    private synchronized void rec(String s) {
        lines.add(s);
        Log.i(TAG, s);
        try {
            if (out == null) {
                File f = new File(getFilesDir(), "wvprobe-result.txt");
                out = new FileOutputStream(f, false);
                Log.i(TAG, "# WebViewProbe v1.10, filesDir=" + getFilesDir());
            }
            out.write((s + "\n").getBytes("UTF-8"));
            out.flush();
        } catch (Throwable ignore) {
        }
    }

    // ---------------------------------------------------------------- 主流程（主线程）

    private void runAll(LinearLayout root) {
        // 0) 静态检查：Context / Assets / MIME 表
        // 1) 先做不依赖 WebView 的静态检查
        probeMimeTables();
        probeAllApkPaths();
        probeHostAssetsContainWebView();
        probeSharedLibResource();

        // 2) 建 WebView（主线程）
        try {
            wv = new WebView(this);
            rec("W0 ok: WebView created on main thread");
            try {
                rec("W0 settings ua=" + brief(wv.getSettings().getUserAgentString()));
            } catch (Throwable t) {
                rec("W0 settings FAIL: " + t);
            }
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, 500);
            root.addView(wv, lp);
            wv.getSettings().setAllowFileAccess(true);
            wv.getSettings().setAllowContentAccess(true);
            wv.getSettings().setJavaScriptEnabled(true);
        } catch (Throwable t) {
            rec("W0 FAIL: new WebView() threw " + t.getClass().getSimpleName() + ": " + t.getMessage());
            rec("DONE (aborted, no WebView)");
            return;
        }

        // 2.5) WebView 已建好，这时才能查 Context/Assets 身份
        probeContextIdentity();
        probeAssetsIdentity();

        // 3) 准备文件
        File f1 = prepareFile(HTML_NAME, HTML_BODY);
        File f4 = prepareFile(HTML_W4, HTML_BODY);

        // 3) 串联四个加载实验
        final File ff1 = f1;
        final File ff4 = f4;

        loadAndCheck("W1", "about:blank", HTML_BODY, false, () ->
            loadAndCheck("W2", ff1 == null ? "about:blank" : "file://" + ff1.getAbsolutePath(),
                    null, false, () ->
                loadAndCheck("W3", "file:///android_asset/" + HTML_NAME, null, false, () ->
                    loadAndCheck("W4", ff4 == null ? "about:blank" : "file://" + ff4.getAbsolutePath(),
                            null, false, () -> {
                                // 4) W11：强制在 shouldInterceptRequest 里给 text/html
                                W11_MODE = true;
                                loadAndCheck("W11", ff1 == null ? "about:blank" : "file://" + ff1.getAbsolutePath(),
                                        null, true, () -> {
                                            W11_MODE = false;
                                            rec("DONE all experiments (W11 intercepted " + w11InterceptCount + " request(s))");
                                            status.setText("done, see logcat -s WVP");
                                        });
                            }))));
    }

    private File prepareFile(String name, String body) {
        try {
            File f = new File(getFilesDir(), name);
            FileOutputStream o = new FileOutputStream(f, false);
            o.write(body.getBytes("UTF-8"));
            o.close();
            rec("PREP " + name + " -> " + f.getAbsolutePath()
                    + " exists=" + f.exists() + " len=" + f.length());
            return f;
        } catch (Throwable t) {
            rec("PREP " + name + " FAIL: " + t);
            return null;
        }
    }

    // ---------------------------------------------------------------- 静态检查

    /** W8：WebView 的 Context 到底是谁 */
    private void probeContextIdentity() {
        try {
            Context c = (wv != null) ? wv.getContext() : null;
            if (c == null) {
                rec("W8 skip: WebView 尚未创建（此检查改到 W0 之后才准）");
                return;
            }
            rec("W8 webView.getContext().getPackageName() = " + c.getPackageName());
            rec("W8 this.getPackageName()                    = " + getPackageName());
            rec("W8 context class = " + c.getClass().getName()
                    + " identityHash=" + System.identityHashCode(c));
            rec("W8 this identityHash = " + System.identityHashCode(this));
            rec("W8 SAME_CONTEXT = " + (c == this));
            rec("W8 SAME_PACKAGE = " + c.getPackageName().equals(getPackageName()));
        } catch (Throwable t) {
            rec("W8 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    /** W12：Assets 是否同一个 */
    private void probeAssetsIdentity() {
        try {
            AssetManager mine = getAssets();
            AssetManager theirs = (wv != null) ? wv.getContext().getAssets() : null;
            rec("W12 this.getAssets() identityHash = " + System.identityHashCode(mine));
            rec("W12 wv.context.getAssets() identityHash = "
                    + (theirs == null ? "null(WebView 未创建)" : String.valueOf(System.identityHashCode(theirs))));
            rec("W12 SAME_ASSETS = " + (mine == theirs));
            // 关键：我这边的 assets 里应该有 wvprobe.html
            try {
                InputStream is = mine.open(HTML_NAME);
                rec("W12 this.getAssets().open(" + HTML_NAME + ") OK, available=" + is.available());
                is.close();
            } catch (Throwable t) {
                rec("W12 this.getAssets().open(" + HTML_NAME + ") FAIL: "
                        + t.getClass().getSimpleName());
            }
            if (theirs != null) {
                try {
                    InputStream is = theirs.open(HTML_NAME);
                    rec("W12 wv.context.getAssets().open(" + HTML_NAME + ") OK");
                    is.close();
                } catch (Throwable t) {
                    rec("W12 wv.context.getAssets().open(" + HTML_NAME + ") FAIL: "
                            + t.getClass().getSimpleName());
                }
            }
        } catch (Throwable t) {
            rec("W12 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    /** W9 / W10：MIME 表 */
    private void probeMimeTables() {
        try {
            String m = MimeTypeMap.getSingleton().getMimeTypeFromExtension("html");
            rec("W9 MimeTypeMap.getMimeTypeFromExtension(\"html\") = " + m);
        } catch (Throwable t) {
            rec("W9 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
        try {
            String m = MimeTypeMap.getFileExtensionFromUrl("file:///a/b/x.html");
            rec("W9 getFileExtensionFromUrl(file:///a/b/x.html) = " + m);
        } catch (Throwable t) {
            rec("W9b FAIL: " + t);
        }
        try {
            String m = URLConnection.guessContentTypeFromName("x.html");
            rec("W10 URLConnection.guessContentTypeFromName(\"x.html\") = " + m);
        } catch (Throwable t) {
            rec("W10 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
        try {
            String m = URLConnection.guessContentTypeFromName("x.txt");
            rec("W10b guessContentTypeFromName(\"x.txt\") = " + m);
        } catch (Throwable t) {
            rec("W10b FAIL: " + t);
        }
    }

    private void probeAllApkPaths() {
        try {
            ApplicationInfo ai = getApplicationInfo();
            Method m = ApplicationInfo.class.getMethod("getAllApkPaths");
            Object r = m.invoke(ai);
            if (r instanceof String[]) {
                String[] paths = (String[]) r;
                rec("W5 getAllApkPaths() n=" + paths.length);
                for (String p : paths) {
                    rec("W5   " + p + " exists=" + new File(p).exists());
                }
            } else {
                rec("W5 odd: " + r);
            }
        } catch (NoSuchMethodException e) {
            rec("W5 n/a: 无 getAllApkPaths（API " + Build.VERSION.SDK_INT + "）");
        } catch (Throwable t) {
            rec("W5 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    /** W6：宿主 AssetManager 里到底有什么 WebView 资源 */
    private void probeHostAssetsContainWebView() {
        AssetManager am = getAssets();
        rec("W6 getAssets() class=" + am.getClass().getName()
                + " identityHash=" + System.identityHashCode(am));
        // 打印 webkit/ 的实际内容（v1.9 只打了数量，看不出是什么）
        try {
            String[] list = am.list("webkit");
            if (list == null) {
                rec("W6 list(\"webkit\") -> null");
            } else {
                rec("W6 list(\"webkit\") -> " + list.length + " entries");
                for (int i = 0; i < Math.min(list.length, 20); i++) {
                    rec("W6   webkit/" + list[i]);
                }
            }
        } catch (Throwable t) {
            rec("W6 list(\"webkit\") FAIL: " + t.getClass().getSimpleName());
        }
        for (String p : new String[] { "", "webkit/", "webview_assets.json" }) {
            try {
                String[] list = am.list(p);
                rec("W6 list(\"" + p + "\") -> " + (list == null ? "null" : list.length + " entries"));
            } catch (Throwable t) {
                rec("W6 list(\"" + p + "\") threw " + t.getClass().getSimpleName());
            }
        }
    }

    private void probeSharedLibResource() {
        Resources res = getResources();
        for (int id : new int[] { 0x020c0006, 0x020c0000 }) {
            try {
                int v = res.getInteger(id);
                rec(String.format("W7 ok: getInteger(0x%08x) = %d", id, v));
            } catch (Throwable t) {
                rec(String.format("W7 FAIL: getInteger(0x%08x) -> %s", id, t.getClass().getSimpleName()));
            }
            try {
                android.util.TypedValue tv = new android.util.TypedValue();
                res.getValue(id, tv, true);
                rec(String.format("W7 probe: getValue(0x%08x) t=0x%x cookie=%d", id, tv.type, tv.assetCookie));
            } catch (Throwable t) {
                rec(String.format("W7 probe: getValue(0x%08x) -> %s", id, t.getClass().getSimpleName()));
            }
        }
    }

    // ---------------------------------------------------------------- 加载实验

    /** W11 模式开关：true 时在 shouldInterceptRequest 里对 file:// 主动返回 text/html */
    private boolean W11_MODE = false;

    private interface Next { void run(); }

    private void loadAndCheck(String tag, String url, String htmlBody, boolean markW11, Next next) {
        final CountDownLatch latch = new CountDownLatch(1);
        final AtomicReference<String> outcome = new AtomicReference<>("timeout(8s)");
        final int beforeIntercepts = w11InterceptCount;

        wv.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest req) {
                Uri u = req == null ? null : req.getUrl();
                rec(tag + " INTERCEPT url=" + u
                        + " method=" + (req == null ? "?" : req.getMethod()));
                if (W11_MODE && u != null && "file".equals(u.getScheme())) {
                    try {
                        File f = new File(u.getPath());
                        if (f.exists()) {
                            w11InterceptCount++;
                            rec(tag + " INTERCEPT -> 主动返回 text/html, file len=" + f.length());
                            return new WebResourceResponse(
                                    "text/html", "utf-8", new java.io.FileInputStream(f));
                        }
                        rec(tag + " INTERCEPT -> 文件不存在，放弃接管");
                    } catch (Throwable t) {
                        rec(tag + " INTERCEPT -> 接管失败: " + t);
                    }
                }
                return null;
            }

            @Override
            public void onPageFinished(WebView v, String u) {
                v.evaluateJavascript(
                        "(document.body && document.body.innerText) || 'EMPTY'",
                        value -> {
                            outcome.set("finished body=" + brief(value));
                            latch.countDown();
                        });
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                outcome.set("error code=" + err.getErrorCode()
                        + " desc=" + err.getDescription()
                        + " url=" + (req == null ? "?" : req.getUrl()));
                latch.countDown();
            }
        });

        try {
            if (htmlBody != null) {
                wv.loadDataWithBaseURL(url, htmlBody, "text/html", "utf-8", null);
            } else {
                wv.loadUrl(url);
            }
        } catch (Throwable t) {
            outcome.set("load threw " + t.getClass().getSimpleName() + ": " + t.getMessage());
            latch.countDown();
        }

        // 用主线程 Handler 轮询结果（不能阻塞主线程）
        final long deadline = System.currentTimeMillis() + 8000;
        final Runnable[] poll = new Runnable[1];
        poll[0] = () -> {
            if (latch.getCount() == 0) {
                String o = outcome.get();
                int inter = w11InterceptCount - beforeIntercepts;
                rec(tag + " url=" + url + " -> " + o
                        + (inter > 0 ? "  [intercepted " + inter + "]" : "")
                        + (markW11 ? "  [W11_MODE]" : ""));
                main.post(next::run);
            } else if (System.currentTimeMillis() > deadline) {
                rec(tag + " url=" + url + " -> timeout(8s)");
                main.post(next::run);
            } else {
                main.postDelayed(poll[0], 200);
            }
        };
        main.postDelayed(poll[0], 200);
    }

    private static String brief(String s) {
        if (s == null) return "null";
        s = s.replace('\n', ' ');
        s = s.replace("\u003C", "<");
        return s.length() <= 160 ? s : s.substring(0, 160) + "...";
    }
}
