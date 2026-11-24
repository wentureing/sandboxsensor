package com.sandbox.probe;

import android.app.Activity;
import android.content.pm.ApplicationInfo;
import android.content.res.AssetManager;
import android.content.res.Resources;
import android.graphics.Bitmap;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * WebViewProbeActivity —— 用实践证明 WebView 在容器里的行为。
 *
 * 设计原则：**每组实验只改一个变量**，逐个跑，结果逐条打日志。
 *
 * 实验矩阵：
 *   W0  WebView 能否创建 + getSettings()        -> 测 WebView 自己的资源（0x02 域）能否解析
 *   W1  loadDataWithBaseURL(about:blank)        -> 测渲染，完全不碰文件
 *   W2  loadUrl(file:// + getFilesDir())        -> 测 file:// 读私有目录
 *   W3  loadUrl(file:///android_asset/)         -> 测 assets（AssetManager 路径）
 *   W4  loadUrl(file:// + getDataDir()/files)   -> 测 dataDir 形式
 *   W5  ApplicationInfo.getAllApkPaths()        -> AOSP WebViewFactory 用它遍历 APK 加 shared library
 *   W6  宿主 getAssets() 里能不能找到 WebView 资源 -> 验证 addAssetPathAsSharedLibrary 是否生效
 *   W7  Resources 里 0x02 域的资源能否解析       -> 直接验证 popup 崩溃那个 0x20c0006
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
    private static final String HTML_BODY =
            "<html><body><h1 id='h'>WVPAGE_OK</h1><p>hello from probe</p></body></html>";

    private final List<String> lines = new ArrayList<>();
    private FileOutputStream out;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 极简布局：一个 TextView 显示进度 + 一个容器放 WebView
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        TextView status = new TextView(this);
        status.setText("WebViewProbe running...");
        root.addView(status, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(root);

        Thread t = new Thread(() -> runAll(root, status), "wvprobe");
        t.start();
    }

    // ---------------------------------------------------------------- 记录

    private synchronized void rec(String s) {
        lines.add(s);
        Log.i(TAG, s);
        try {
            if (out == null) {
                File f = new File(getFilesDir(), "wvprobe-result.txt");
                out = new FileOutputStream(f, false);
                rec0("# WebViewProbe result, filesDir=" + getFilesDir());
            }
            out.write((s + "\n").getBytes("UTF-8"));
            out.flush();
        } catch (Throwable ignore) {
            // 结果主要看 logcat，文件写失败不致命
        }
    }

    private void rec0(String s) {
        Log.i(TAG, s);
    }

    // ---------------------------------------------------------------- 主流程

    private void runAll(LinearLayout root, TextView status) {
        try {
            // 准备一个 html 文件（写在 guest 视角的 filesDir 里）
            File html = prepareHtml();

            // W0：WebView 能否创建（资源域 0x02 是否可用）
            WebView wv = null;
            try {
                wv = createWebView();
                if (wv != null) {
                    String ua = wv.getSettings().getUserAgentString();
                    rec("W0 ok: WebView created, ua=" + brief(ua));
                    rec("W0b ok: settings readable (证明 WebView 自己的资源可解析)");
                } else {
                    rec("W0 FAIL: new WebView() returned null");
                }
            } catch (Throwable t) {
                rec("W0 FAIL: new WebView() threw " + t.getClass().getSimpleName() + ": " + t.getMessage());
            }

            // W5：ApplicationInfo.getAllApkPaths() —— AOSP WebViewFactory 用它加 shared library
            probeAllApkPaths();

            // W6：宿主的 AssetManager 里有没有 WebView 的资源
            probeHostAssetsContainWebView();

            // W7：0x02 域的资源能否解析（popup 崩溃那个 0x20c0006）
            probeSharedLibResource();

            // W1-W4：四个加载实验（需要 wv）
            if (wv != null) {
                final WebView fwv = wv;
                runOnUiThread(() -> {
                    LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT, 600);
                    root.addView(fwv, lp);
                });

                // W1：loadDataWithBaseURL —— 完全不碰文件
                loadAndCheck("W1", wv, "about:blank", HTML_BODY, null);

                // W2：file:// + filesDir
                if (html != null) {
                    loadAndCheck("W2", wv, "file://" + html.getAbsolutePath(), null, null);
                } else {
                    rec("W2 skip: 无法创建 " + HTML_NAME);
                }

                // W3：file:///android_asset/
                loadAndCheck("W3", wv, "file:///android_asset/" + HTML_NAME, null, null);

                // W4：file:// + dataDir/files
                File viaDataDir = new File(getDataDir(), "files/" + HTML_NAME);
                copy(new File(getFilesDir(), HTML_NAME), viaDataDir);
                loadAndCheck("W4", wv, "file://" + viaDataDir.getAbsolutePath(), null, null);
            }

            rec("DONE all experiments");
            runOnUiThread(() -> status.setText("done, see logcat -s WVP"));
        } catch (Throwable t) {
            rec("FATAL in runAll: " + t);
            Log.e(TAG, "runAll failed", t);
        }
    }

    // ---------------------------------------------------------------- 各实验

    private File prepareHtml() {
        try {
            File f = new File(getFilesDir(), HTML_NAME);
            FileOutputStream o = new FileOutputStream(f, false);
            o.write(HTML_BODY.getBytes("UTF-8"));
            o.close();
            rec("PREP html written: " + f.getAbsolutePath()
                    + " exists=" + f.exists() + " len=" + f.length());
            return f;
        } catch (Throwable t) {
            rec("PREP FAIL: " + t);
            return null;
        }
    }

    private void copy(File from, File to) {
        try {
            to.getParentFile().mkdirs();
            FileOutputStream o = new FileOutputStream(to, false);
            byte[] buf = new byte[4096];
            java.io.FileInputStream in = new java.io.FileInputStream(from);
            int n;
            while ((n = in.read(buf)) > 0) o.write(buf, 0, n);
            in.close();
            o.close();
            rec("PREP copy -> " + to.getAbsolutePath() + " len=" + to.length());
        } catch (Throwable t) {
            rec("PREP copy FAIL -> " + to.getAbsolutePath() + ": " + t);
        }
    }

    private void probeAllApkPaths() {
        try {
            ApplicationInfo ai = getApplicationInfo();
            // API 30+ 有 getAllApkPaths()
            Method m = ApplicationInfo.class.getMethod("getAllApkPaths");
            Object r = m.invoke(ai);
            if (r instanceof String[]) {
                String[] paths = (String[]) r;
                rec("W5 ok: getAllApkPaths() n=" + paths.length);
                for (String p : paths) {
                    rec("W5   apkPath: " + p + " exists=" + new File(p).exists());
                }
            } else {
                rec("W5 odd: getAllApkPaths() returned " + r);
            }
        } catch (NoSuchMethodException e) {
            rec("W5 n/a: ApplicationInfo.getAllApkPaths() 不存在（API " + Build.VERSION.SDK_INT + "）");
            rec("W5   fallback sourceDir=" + getApplicationInfo().sourceDir);
            rec("W5   fallback splitSourceDirs=" + java.util.Arrays.toString(getApplicationInfo().splitSourceDirs));
        } catch (Throwable t) {
            rec("W5 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    /**
     * W6：AOSP WebViewFactory 会把 WebView 的 APK 通过
     *     initialApplication.getAssets().addAssetPathAsSharedLibrary(path)
     *     加进宿主 AssetManager。这里检查它到底有没有生效。
     *
     * 判据：能在 assets 里找到 WebView 的某个已知资源文件名。
     */
    private void probeHostAssetsContainWebView() {
        AssetManager am = getAssets();
        rec("W6 info: getAssets() class=" + am.getClass().getName()
                + " hashCode=" + System.identityHashCode(am));

        String[] probes = {
                "webkit/",
                "webview_assets.json",
                "ic_webkit_",
        };
        for (String p : probes) {
            try {
                String[] list = am.list(p);
                rec("W6 list(" + p + ") -> " + (list == null ? "null" : list.length + " entries"));
            } catch (Throwable t) {
                rec("W6 list(" + p + ") threw " + t.getClass().getSimpleName());
            }
        }

        // 关键判据：宿主 assets 里能否打开 WebView APK 特有的文件
        try {
            java.io.InputStream is = am.open("webview_assets.json");
            rec("W6 HIT: assets.open(webview_assets.json) 成功 -> WebView shared library 已加入宿主 AssetManager");
            is.close();
        } catch (Throwable t) {
            rec("W6 MISS: assets.open(webview_assets.json) 失败（"
                    + t.getClass().getSimpleName() + "）-> 宿主 AssetManager 里没有 WebView 资源");
        }
    }

    /**
     * W7：直接试 popup 崩溃里那个资源 ID 0x20c0006。
     *     用 Resources.getInteger / getValue 探 0x02 域是否可解析。
     */
    private void probeSharedLibResource() {
        Resources res = getResources();
        int[] ids = { 0x020c0006, 0x020c0000, 0x020c0001 };
        for (int id : ids) {
            try {
                int v = res.getInteger(id);
                rec(String.format("W7 ok: getInteger(0x%08x) = %d", id, v));
            } catch (Throwable t) {
                rec(String.format("W7 FAIL: getInteger(0x%08x) -> %s: %s",
                        id, t.getClass().getSimpleName(), brief(String.valueOf(t.getMessage()))));
            }
            try {
                android.util.TypedValue tv = new android.util.TypedValue();
                boolean got = res.getValue(id, tv, true);
                rec(String.format("W7 probe: getValue(0x%08x) got=%b t=0x%x d=0x%x cookie=%d",
                        id, got, tv.type, tv.data, tv.assetCookie));
            } catch (Throwable t) {
                rec(String.format("W7 probe: getValue(0x%08x) -> %s",
                        id, t.getClass().getSimpleName()));
            }
        }
    }

    /**
     * 加载并用 JS 读回 body 文本作为判据。
     */
    private void loadAndCheck(String tag, WebView wv, String url, String htmlBody, String mime) {
        final CountDownLatch latch = new CountDownLatch(1);
        final AtomicReference<String> outcome = new AtomicReference<>("timeout(8s)");

        runOnUiThread(() -> {
            wv.setWebViewClient(new WebViewClient() {
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

                @Override
                public void onReceivedError(WebView v, int code, String desc, String failingUrl) {
                    outcome.set("error(legacy) code=" + code + " desc=" + desc + " url=" + failingUrl);
                    latch.countDown();
                }
            });
            try {
                wv.getSettings().setAllowFileAccess(true);
                wv.getSettings().setAllowContentAccess(true);
                wv.getSettings().setJavaScriptEnabled(true);
                if (htmlBody != null) {
                    wv.loadDataWithBaseURL(url, htmlBody, mime != null ? mime : "text/html", "utf-8", null);
                } else {
                    wv.loadUrl(url);
                }
            } catch (Throwable t) {
                outcome.set("load threw " + t.getClass().getSimpleName() + ": " + t.getMessage());
                latch.countDown();
            }
        });

        try {
            latch.await(8, TimeUnit.SECONDS);
        } catch (InterruptedException ignore) {
        }
        rec(tag + " url=" + url + " -> " + outcome.get());
    }

    private WebView createWebView() {
        final AtomicReference<WebView> ref = new AtomicReference<>();
        final AtomicReference<Throwable> err = new AtomicReference<>();
        final CountDownLatch latch = new CountDownLatch(1);
        runOnUiThread(() -> {
            try {
                ref.set(new WebView(WebViewProbeActivity.this));
            } catch (Throwable t) {
                err.set(t);
            } finally {
                latch.countDown();
            }
        });
        try {
            latch.await(10, TimeUnit.SECONDS);
        } catch (InterruptedException ignore) {
        }
        if (err.get() != null) throw new RuntimeException(err.get());
        return ref.get();
    }

    private static String brief(String s) {
        if (s == null) return "null";
        s = s.replace('\n', ' ');
        return s.length() <= 120 ? s : s.substring(0, 120) + "...";
    }
}
