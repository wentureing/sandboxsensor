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
 * v1.12 追加（W14 已证明"沙箱路径"是唯一变量，本轮分离"路径前缀"与"谁写的文件"）：
 *   W14  file:///data/local/tmp/wvprobe_tmp.html      <- 非沙箱路径，正常渲染
 *   W17  file://<filesDir>/pushedup.html              <- 沙箱路径 + 文件由 adb push（排除写入者变量）
 *   W18  file:///data/user/0/com.nextvm.app/files/... <- 容器私有目录根（不经 virtual/data）
 *   W19  file:///sdcard/wvprobe19.html                <- 被容器重定向的公共目录
 *   所有实验的 onPageFinished 都追加 document.contentType，用来直接确认 MIME
 *
 * v1.13 追加（W30：模拟容器的 client 包装 —— 修复方案 A 的预演）：
 *   容器将来的修法是「应用 setWebViewClient 之后，反射把 WebViewClient 字段换成包装 client」。
 *   W30 用**同样的反射手法**验证三件事：
 *     ① WebView 里能否反射找到 WebViewClient 类型字段
 *     ② 包装后内层（应用的）client 还会不会被调用
 *     ③ 包装补 MIME 后沙箱路径的 HTML 能否正常渲染
 *
 * v1.13 另两处改动（Gaia 场景 —— Gaia 里没有命令行，只能点图标）：
 *   ① 本 Activity 承担 LAUNCHER：点桌面图标 = 打开本页 = 自动跑完全部实验
 *   ② 结果实时显示在屏幕上（等宽字体、可长按选中复制），并同时写 3 个候选路径的文件：
 *        filesDir / externalRoot(/sdcard) / externalFiles
 *      每个路径的 OK / FAIL（含异常名）都会打出来 —— 这本身就是权限传导的证据
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
    private final Handler main = new Handler(Looper.getMainLooper());

    private WebView wv;
    private TextView status;
    /** v1.13：屏幕上实时显示的结果区（Gaia 这类容器里没有 adb，屏幕是唯一取结果方式） */
    private TextView logView;
    private android.widget.ScrollView logScroll;
    private int w11InterceptCount = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);

        status = new TextView(this);
        status.setTextSize(13);
        status.setPadding(12, 12, 12, 6);
        status.setText("WebViewProbe v1.22 —— 打开即自动跑全部 W 实验，结果实时显示在下方");
        root.addView(status, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        // 结果区：等宽字体 + 可长按选中复制（找不到 adb 时可以直接复制出去）
        logView = new TextView(this);
        logView.setTextSize(8);
        logView.setTypeface(android.graphics.Typeface.MONOSPACE);
        logView.setTextIsSelectable(true);
        logView.setPadding(8, 4, 8, 4);
        logScroll = new android.widget.ScrollView(this);
        logScroll.addView(logView);
        root.addView(logScroll, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

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

    // v1.13：结果要落成文件。Gaia 这类容器没有 adb，所以对每个候选路径都试一遍，
    // 并把「哪个成功 / 哪个失败（含 errno）」打到屏幕上 —— 这本身就是权限传导的证据。
    private boolean outputsInitialized = false;
    private boolean outInfoWritten = false;
    private String outInfo = "";
    private final List<FileOutputStream> outs = new ArrayList<>();

    private void openOutputs() {
        List<File> candidates = new ArrayList<>();
        List<String> labels = new ArrayList<>();

        // 1) 应用私有目录（容器里 run-as 可读；Gaia 落在 gaia 沙箱镜像里）
        try {
            candidates.add(new File(getFilesDir(), "wvprobe-result.txt"));
            labels.add("filesDir");
        } catch (Throwable ignore) {
        }
        // 2) 外部存储 /sdcard 根（guest 视角；Gaia 里会被重定向，且可能需要 MANAGE_EXTERNAL_STORAGE）
        try {
            File ext = android.os.Environment.getExternalStorageDirectory();
            if (ext != null) {
                candidates.add(new File(ext, "wvprobe-result.txt"));
                labels.add("externalRoot");
            }
        } catch (Throwable ignore) {
        }
        // 3) 外部私有目录（应用专属，通常无需额外权限）
        try {
            File ef = getExternalFilesDir(null);
            if (ef != null) {
                candidates.add(new File(ef, "wvprobe-result.txt"));
                labels.add("externalFiles");
            }
        } catch (Throwable ignore) {
        }

        StringBuilder sb = new StringBuilder();
        sb.append("OUT 输出目标（" + candidates.size() + " 个候选）\n");
        for (int i = 0; i < candidates.size(); i++) {
            File f = candidates.get(i);
            String label = i < labels.size() ? labels.get(i) : "?";
            try {
                File dir = f.getParentFile();
                if (dir != null && !dir.exists()) dir.mkdirs();
                FileOutputStream o = new FileOutputStream(f, false);
                outs.add(o);
                sb.append("OUT  [").append(label).append("] ").append(f.getAbsolutePath()).append("  OK\n");
            } catch (Throwable t) {
                sb.append("OUT  [").append(label).append("] ").append(f.getAbsolutePath())
                        .append("  FAIL ").append(t.getClass().getSimpleName())
                        .append(": ").append(t.getMessage()).append('\n');
            }
        }
        outInfo = sb.toString();
        Log.i(TAG, outInfo.replace("\n", " | "));
    }

    private synchronized void rec(String s) {
        lines.add(s);
        Log.i(TAG, s);
        if (!outputsInitialized) {
            outputsInitialized = true;
            try {
                openOutputs();
            } catch (Throwable ignore) {
            }
        }
        for (FileOutputStream o : outs) {
            try {
                o.write((s + "\n").getBytes("UTF-8"));
                o.flush();
            } catch (Throwable ignore) {
            }
        }
        // v1.13：同时打到屏幕上（后到先排，保持与文件/logcat 相同顺序）
        final String line = s;
        main.post(() -> {
            if (logView != null) logView.append(line + "\n");
            if (logScroll != null) logScroll.post(() -> logScroll.fullScroll(android.view.View.FOCUS_DOWN));
        });

        // 输出目标这条信息放在第一行之后写（避免自己写自己时的顺序问题）
        if (!outInfoWritten && !outInfo.isEmpty()) {
            outInfoWritten = true;
            final String info = outInfo;
            for (FileOutputStream o : outs) {
                try {
                    o.write(info.getBytes("UTF-8"));
                    o.flush();
                } catch (Throwable ignore) {
                }
            }
            main.post(() -> {
                if (logView != null) logView.append(info);
            });
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
            // v1.13：WebView 只留 200px —— 结果以日志区为主（W 系列的结论都从 innerText/contentType 取）
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, 200);
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
        // v1.14: WebView **内部**看到的 application context（chromium ContextUtils）
        probeContextUtils();
        // v1.15: 直接读「进程 Application」是谁 —— 这是 v138 要改的那个根因，
        //        也是 W2/W3 变好或不变的「原因判据」（否则没生效和生效没修好看起来一样）
        probeProcessApplication();

        // 3) 准备文件
        File f1 = prepareFile(HTML_NAME, HTML_BODY);
        File f4 = prepareFile(HTML_W4, HTML_BODY);

        // 3) 串联加载实验
        final File ff1 = f1;
        final File ff4 = f4;
        // v1.20：确认 WebView 视角的 dataDir 与 W2 文件路径的归属关系
        probeDataDir(ff1 == null ? null : ff1.getAbsolutePath());
        // v1.21：假 dataDir 是否生效 + Java 层能否用它读文件（v139 的两个判据）
        probeDataDirUsability();
        // v1.22：Java 层文件 I/O 重定向（容器 v141 RegisterNatives）是否生效
        probeJavaIoRedirect();
        // v1.16：直接问 WebView 内部的 AndroidProtocolHandler，把「W2 为什么 text/plain」定死
        probeProtocolHandler(ff1);
        // W16 用 .txt 扩展名（内容仍是 HTML），测"是否只看扩展名"
        final File ffTxt = prepareFile("wvprobe_txt.txt", HTML_BODY);

        // W17：沙箱路径 + 文件由 adb push（探针不写），用来排除"文件是谁写的"这个变量。
        //      探针只观察，不创建；文件不存在则记 about:blank。
        final File f17 = new File(getFilesDir(), "pushedup.html");
        rec("PREP W17 pushedup.html -> " + f17.getAbsolutePath()
                + " exists=" + f17.exists()
                + " len=" + (f17.exists() ? f17.length() : -1));
        // W18：容器私有目录根（不经 virtual/data 路径段）
        final File f18 = prepAt("/data/user/0/com.nextvm.app/files/wvprobe18.html");
        // W19：被容器重定向的公共目录
        final File f19 = prepAt("/sdcard/wvprobe19.html");
        // v1.19：沙箱 + 有 BOM 的对照文件（W2 是无 BOM 的同一个 body）
        final File fBom = prepareFileRaw("wvprobe_bom.html", HTML_BODY, true);

        loadAndCheck("W1", "about:blank", HTML_BODY, false, () ->
            loadAndCheck("W2", ff1 == null ? "about:blank" : "file://" + ff1.getAbsolutePath(),
                    null, false, () ->
                loadAndCheck("W3", "file:///android_asset/" + HTML_NAME, null, false, () ->
                    loadAndCheck("W4", ff4 == null ? "about:blank" : "file://" + ff4.getAbsolutePath(),
                            null, false, () ->
                    // W14：/data/local/tmp（容器不该改写的路径），文件由 adb push 准备。
                    //      能渲染 -> 根因在"沙箱路径"；也失败 -> 根因在更底层。
                    loadAndCheck("W14", "file:///data/local/tmp/wvprobe_tmp.html", null, false, () ->
                    // W15：沙箱路径 + ?x=.html，测 Chromium 是否按 URL 末段判扩展名
                    loadAndCheck("W15", (ff1 == null ? "about:blank"
                                    : "file://" + ff1.getAbsolutePath() + "?x=.html"),
                            null, false, () ->
                    // W16：沙箱 .txt（内容是 HTML），测"只看扩展名"是否成立
                    loadAndCheck("W16", ffTxt == null ? "about:blank"
                                    : "file://" + ffTxt.getAbsolutePath(), null, false, () ->
                    // W17：沙箱路径 + 文件由 adb push（探针不写），排除"谁写的文件"这个变量
                    loadAndCheck("W17", f17 == null || !f17.exists() ? "about:blank"
                            : "file://" + f17.getAbsolutePath(), null, false, () ->
                    // W18：容器私有目录根（不经 virtual/data），测是否与 virtual 路径段有关
                    loadAndCheck("W18", f18 == null ? "about:blank"
                            : "file://" + f18.getAbsolutePath(), null, false, () ->
                    // W19：被容器重定向的公共目录，测"重定向路径"的 MIME 是否也不同
                    loadAndCheck("W19", f19 == null ? "about:blank"
                            : "file://" + f19.getAbsolutePath(), null, false, () ->
                    // v1.19 四组合：把「BOM」这个干扰项控制住
                    //   W2  = 沙箱 + 无 BOM  -> text/plain
                    //   W14 = tmp  + 有 BOM  -> text/html
                    //   W35a= 沙箱 + 有 BOM  /  W35b= tmp + 无 BOM
                    // 若 W35a 变 text/html 或 W35b 变 text/plain，则变量是 BOM，与容器无关。
                    loadAndCheck("W35a", fBom == null ? "about:blank"
                            : "file://" + fBom.getAbsolutePath(), null, false, () ->
                    loadAndCheck("W35b", "file:///data/local/tmp/wvprobe_nobom.html",
                            null, false, () -> {
                                // W11：强制在 shouldInterceptRequest 里给 text/html
                                W11_MODE = true;
                                loadAndCheck("W11", ff1 == null ? "about:blank" : "file://" + ff1.getAbsolutePath(),
                                        null, true, () -> {
                                            W11_MODE = false;
                                            rec("W11 结束（W11_MODE 已复位），接着跑 W30");
                                            // v1.13：W30 必须被调用 —— 它验证「反射包装 client」这条路
                                            runW30(ff1, () -> {
                                                rec("DONE all experiments (W11 intercepted " + w11InterceptCount + " request(s))");
                                                dumpReportToLogcat();
                                                status.setText("✅ 全部实验完成 —— 可长按下方文字复制");
                                            });
                                        });
                            }))))))))))));
    }

    /**
     * v1.13：跑完把完整报告重打一遍 logcat。
     *
     * 为什么需要：Gaia 这类容器的沙箱 adb 读不到（内部路径 Permission denied、
     * run-as 不可用、无 root），**logcat 是唯一设备级的出口**。逐行打的日志可能
     * 被缓冲冲掉，所以结尾再整篇重放一次，一次 `adb logcat -d -s WVP` 就能拿全。
     */
    private void dumpReportToLogcat() {
        List<String> snapshot;
        synchronized (this) {
            snapshot = new ArrayList<>(lines);
        }
        Log.i(TAG, "===== WVP REPORT BEGIN (" + snapshot.size() + " lines) =====");
        for (String l : snapshot) {
            Log.i(TAG, "R| " + l);
        }
        Log.i(TAG, "===== WVP REPORT END =====");
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

    /** v1.19：与 prepareFile 相同，但可以决定是否写 UTF-8 BOM（用于 W35 控制 BOM 变量） */
    private File prepareFileRaw(String name, String body, boolean bom) {
        try {
            File f = new File(getFilesDir(), name);
            FileOutputStream o = new FileOutputStream(f, false);
            if (bom) {
                o.write(new byte[] { (byte) 0xEF, (byte) 0xBB, (byte) 0xBF });
            }
            o.write(body.getBytes("UTF-8"));
            o.close();
            rec("PREP " + name + " -> " + f.getAbsolutePath()
                    + " exists=" + f.exists() + " len=" + f.length() + " bom=" + bom);
            return f;
        } catch (Throwable t) {
            rec("PREP " + name + " FAIL: " + t);
            return null;
        }
    }

    /** W18/W19 用：把 HTML 写到指定绝对路径；失败也返回 File，便于后续观察 exists/len */
    private File prepAt(String absPath) {
        File f = new File(absPath);
        try {
            File dir = f.getParentFile();
            if (dir != null) dir.mkdirs();
            FileOutputStream o = new FileOutputStream(f, false);
            o.write(HTML_BODY.getBytes("UTF-8"));
            o.close();
            rec("PREP " + absPath + " OK exists=" + f.exists() + " len=" + f.length());
        } catch (Throwable t) {
            rec("PREP " + absPath + " FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
        return f;
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

    /** W31：WebView 内部（chromium）认为的 application context 是谁 */
    private void probeContextUtils() {
        try {
            Class<?> factory = Class.forName("android.webkit.WebViewFactory");
            Method gp = factory.getDeclaredMethod("getProviderClass");
            gp.setAccessible(true);
            Class<?> providerClass = (Class<?>) gp.invoke(null);
            rec("W31 providerClass = " + (providerClass == null ? "null" : providerClass.getName()));
            if (providerClass == null) {
                rec("W31 skip: providerClass 为 null（WebView 还没初始化？）");
                return;
            }
            ClassLoader cl = providerClass.getClassLoader();
            rec("W31 glue classloader = " + cl);
            Class<?> cu = Class.forName("org.chromium.base.ContextUtils", false, cl);
            // 静态字段名多候选（随 WebView 版本可能变）
            for (String fn : new String[] { "sApplicationContext", "sAppContext" }) {
                try {
                    java.lang.reflect.Field f = cu.getDeclaredField(fn);
                    f.setAccessible(true);
                    Object v = f.get(null);
                    if (v instanceof Context) {
                        Context c = (Context) v;
                        rec("W31 field " + fn + " = " + c.getPackageName()
                                + " (" + c.getClass().getName() + ")");
                    } else {
                        rec("W31 field " + fn + " = " + v);
                    }
                } catch (NoSuchFieldException e) {
                    rec("W31 field " + fn + " 不存在");
                }
            }
            try {
                Method gm = cu.getMethod("getApplicationContext");
                Object c = gm.invoke(null);
                if (c instanceof Context) {
                    Context ctx = (Context) c;
                    rec("W31 getApplicationContext().packageName = " + ctx.getPackageName());
                    rec("W31 getApplicationContext() class = " + ctx.getClass().getName());
                    try {
                        rec("W31 getApplicationContext().getFilesDir = " + ctx.getFilesDir());
                    } catch (Throwable t) {
                        rec("W31 getFilesDir FAIL: " + t.getClass().getSimpleName());
                    }
                } else {
                    rec("W31 getApplicationContext() = " + c);
                }
            } catch (Throwable t) {
                rec("W31 getApplicationContext() FAIL: " + t.getClass().getSimpleName()
                        + ": " + t.getMessage());
            }
        } catch (Throwable t) {
            rec("W31 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    /**
     * W32：直接读「这个进程的 Application」是谁。
     *
     * 这是 WebView context 根因的**原因判据**：framework 里
     * `WebViewFactory.getWebViewContextAndSetProvider()` -> `AppGlobals.getInitialApplication()`
     * -> `ActivityThread.getApplication()` -> `mInitialApplication`，
     * 而 `WebViewDelegate` 走 `ActivityThread.currentApplication()`，同一个字段。
     *
     * 只反射 framework 的公开类，不碰 WebView 的混淆类（W31 就栽在这上面）。
     */
    private void probeProcessApplication() {
        try {
            Class<?> at = Class.forName("android.app.ActivityThread");
            try {
                Object app = at.getMethod("currentApplication").invoke(null);
                reportApp("W32 ActivityThread.currentApplication()", app);
            } catch (Throwable t) {
                rec("W32 currentApplication() FAIL: " + t.getClass().getSimpleName()
                        + ": " + t.getMessage());
            }
            try {
                Class<?> ag = Class.forName("android.app.AppGlobals");
                Object app = ag.getMethod("getInitialApplication").invoke(null);
                reportApp("W32 AppGlobals.getInitialApplication()", app);
            } catch (Throwable t) {
                rec("W32 getInitialApplication() FAIL: " + t.getClass().getSimpleName()
                        + ": " + t.getMessage());
            }
            // mInitialApplication 字段原值（确认替换是否真的落到字段上）
            try {
                Object thread = at.getMethod("currentActivityThread").invoke(null);
                java.lang.reflect.Field f = at.getDeclaredField("mInitialApplication");
                f.setAccessible(true);
                reportApp("W32 field mInitialApplication", f.get(thread));
            } catch (Throwable t) {
                rec("W32 mInitialApplication 读不到: " + t.getClass().getSimpleName());
            }
        } catch (Throwable t) {
            rec("W32 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
        // W33：WebView 的数据目录后缀 —— 多实例是否共用 cookie/localStorage 目录
        try {
            Class<?> wf = Class.forName("android.webkit.WebViewFactory");
            java.lang.reflect.Method m = wf.getDeclaredMethod("getDataDirectorySuffix");
            m.setAccessible(true);
            rec("W33 WebViewFactory.getDataDirectorySuffix() = " + m.invoke(null));
        } catch (Throwable t) {
            rec("W33 getDataDirectorySuffix() FAIL: " + t.getClass().getSimpleName()
                    + ": " + t.getMessage());
        }
    }

    private void reportApp(String tag, Object app) {
        if (app instanceof Context) {
            Context c = (Context) app;
            rec(tag + ".packageName = " + c.getPackageName());
            rec(tag + " class = " + c.getClass().getName());
            try {
                rec(tag + ".getFilesDir = " + c.getFilesDir());
            } catch (Throwable t) {
                rec(tag + ".getFilesDir FAIL: " + t.getClass().getSimpleName());
            }
        } else {
            rec(tag + " = " + app);
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
                // v1.12：同时取 document.contentType —— 直接暴露 WebView 判定的 MIME
                v.evaluateJavascript(
                        "JSON.stringify({ct:(document.contentType||''),"
                                + "body:((document.body && document.body.innerText) || 'EMPTY')})",
                        value -> {
                            outcome.set("finished " + brief(value));
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

    // ------------------------------------------------ W30：模拟容器的 client 包装

    // 目的：容器将来要做的动作是"应用 setWebViewClient 之后，用反射把 mWebViewClient
    // 字段换成一个尊重应用、但对 file:// 补 MIME 的包装 client"。
    // W11 只证明"自己返回 text/html 就能渲染"，没证明"包装别人的 client"这条路可行。
    // W30 用和容器完全一样的反射手法做一遍，验证三件事：
    //   ① WebView 里能不能反射找到 WebViewClient 字段
    //   ② 包装后内层（应用的）client 还会不会被调用
    //   ③ 包装补 MIME 后沙箱路径的 HTML 能不能正常渲染

    private CountDownLatch w30Latch;
    private final AtomicReference<String> w30Outcome = new AtomicReference<>("timeout(8s)");
    private int w30AppCalls = 0;

    /** 与容器将来实现等价的包装 client */
    private final class MimeFixClient extends WebViewClient {
        private final WebViewClient orig;

        MimeFixClient(WebViewClient orig) {
            this.orig = orig;
        }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest req) {
            // 1) 先问应用的 client（Via 处理 homepage.css 走这里）
            WebResourceResponse fromApp = null;
            if (orig != null) {
                try {
                    fromApp = orig.shouldInterceptRequest(v, req);
                } catch (Throwable t) {
                    rec("W30 应用 client 抛异常: " + t);
                }
            }
            if (fromApp != null) {
                rec("W30 应用 client 返回了响应，交回应用");
                return fromApp;
            }
            // 2) 应用返回 null 且是 file:// 才接管
            Uri u = req == null ? null : req.getUrl();
            if (u == null || !"file".equals(u.getScheme())) return null;
            String path = u.getPath();
            if (path == null) return null;
            try {
                if (path.startsWith("/android_asset/")) {
                    String rel = path.substring("/android_asset/".length());
                    rec("W30 接管 asset -> " + mimeFor(rel));
                    return new WebResourceResponse(mimeFor(rel), "utf-8",
                            getAssets().open(rel, AssetManager.ACCESS_STREAMING));
                }
                File f = new File(path);
                if (!f.isFile()) {
                    rec("W30 文件不存在，放弃接管: " + path);
                    return null;
                }
                String mime = mimeFor(path);
                rec("W30 接管 file:// -> " + mime + " len=" + f.length());
                return new WebResourceResponse(mime, "utf-8", new java.io.FileInputStream(f));
            } catch (Throwable t) {
                rec("W30 接管失败: " + t.getClass().getSimpleName() + ": " + t.getMessage());
                return null;
            }
        }

        @Override
        public void onPageFinished(WebView v, String u) {
            if (orig != null) {
                try {
                    orig.onPageFinished(v, u);
                } catch (Throwable ignore) {
                }
            }
            v.evaluateJavascript(
                    "JSON.stringify({ct:(document.contentType||''),"
                            + "body:((document.body && document.body.innerText) || 'EMPTY')})",
                    value -> {
                        w30Outcome.set("finished " + brief(value));
                        if (w30Latch != null) w30Latch.countDown();
                    });
        }

        @Override
        public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
            w30Outcome.set("error code=" + e.getErrorCode() + " desc=" + e.getDescription());
            if (w30Latch != null) w30Latch.countDown();
        }
    }

    private static String mimeFor(String path) {
        String ext = MimeTypeMap.getFileExtensionFromUrl(path.toLowerCase());
        String m = (ext == null || ext.isEmpty()) ? null
                : MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext);
        if (m == null) m = URLConnection.guessContentTypeFromName(path);
        return m != null ? m : "application/octet-stream";
    }

    private void runW30(final File f, final Next next) {
        if (f == null) {
            rec("W30 skip: 文件没准备好");
            next.run();
            return;
        }

        // 1) 应用侧 client（模拟 Via：只接管 css，其余返回 null）
        WebViewClient app = new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
                w30AppCalls++;
                return null;
            }
        };
        wv.setWebViewClient(app);
        rec("W30 应用 client 已设置（模拟 Via）");

        // 2) 容器侧：反射找 WebViewClient 类型字段并包装
        java.lang.reflect.Field target = null;
        for (java.lang.reflect.Field fd : WebView.class.getDeclaredFields()) {
            if (WebViewClient.class.isAssignableFrom(fd.getType())) {
                target = fd;
                break;
            }
        }
        if (target == null) {
            rec("W30 FAIL: WebView 里找不到 WebViewClient 类型字段");
            next.run();
            return;
        }
        try {
            target.setAccessible(true);
            WebViewClient cur = (WebViewClient) target.get(wv);
            rec("W30 字段名=" + target.getName()
                    + " 当前值=" + (cur == null ? "null" : cur.getClass().getName()));
            target.set(wv, new MimeFixClient(cur));
            rec("W30 已包装，开始加载");
        } catch (Throwable t) {
            rec("W30 FAIL: 反射包装失败 " + t.getClass().getSimpleName() + ": " + t.getMessage());
            next.run();
            return;
        }

        // 3) 加载同一个沙箱 URL
        w30Latch = new CountDownLatch(1);
        w30Outcome.set("timeout(8s)");
        final String url = "file://" + f.getAbsolutePath();
        try {
            wv.loadUrl(url);
        } catch (Throwable t) {
            w30Outcome.set("load threw " + t.getClass().getSimpleName());
            w30Latch.countDown();
        }
        final long deadline = System.currentTimeMillis() + 8000;
        final Runnable[] poll = new Runnable[1];
        poll[0] = () -> {
            if (w30Latch.getCount() == 0) {
                rec("W30 url=" + url + " -> " + w30Outcome.get()
                        + "  [应用 client 被调用 " + w30AppCalls + " 次]");
                main.post(next::run);
            } else if (System.currentTimeMillis() > deadline) {
                rec("W30 url=" + url + " -> timeout(8s)  [应用 client 被调用 " + w30AppCalls + " 次]");
                main.post(next::run);
            } else {
                main.postDelayed(poll[0], 200);
            }
        };
        main.postDelayed(poll[0], 200);
    }

    /**
     * W34：直接反射调用 WebView 内部 `org.chromium.android_webview.AndroidProtocolHandler`
     * 的 `open(url)` 与 `getMimeType(stream, url)`。
     *
     * 背景（反汇编得到的事实）：
     *   open(url) 只处理 file 的 asset/resource 前缀与 content，**普通 file 路径返回 null**；
     *   getMimeType 的分支是：content → resolver；file 且 startsWith(assetPath) → 按名字；
     *   **其余 → guessContentTypeFromStream（内容嗅探）**。
     *
     * 所以只要 open(沙箱 html) 返回 null，Java 侧就根本没参与，W2 的 text/plain 只能来自 native；
     * 反之若返回流，说明 assetPath 常量匹配了沙箱路径 —— 两种情况的修法完全不同。
     */
    private void probeProtocolHandler(File sandboxFile) {
        String sandboxUrl = sandboxFile == null ? null : "file://" + sandboxFile.getAbsolutePath();
        String tmpUrl = "file:///data/local/tmp/wvprobe_tmp.html";
        try {
            Class<?> factory = Class.forName("android.webkit.WebViewFactory");
            Method gp = factory.getDeclaredMethod("getProviderClass");
            gp.setAccessible(true);
            Class<?> providerClass = (Class<?>) gp.invoke(null);
            if (providerClass == null) {
                rec("W34 skip: providerClass=null");
                return;
            }
            ClassLoader cl = providerClass.getClassLoader();
            Class<?> aph = Class.forName(
                    "org.chromium.android_webview.AndroidProtocolHandler", false, cl);
            rec("W34 class = " + aph.getName());

            // v1.17：先把真实方法列表打出来 —— 设备上跑的那份 dex 与离线反汇编的签名不一致
            // （v1.16 报 NoSuchMethodException: open(String)）
            try {
                for (Method m : aph.getDeclaredMethods()) {
                    StringBuilder sb = new StringBuilder("W34   m: " + m.getName() + "(");
                    Class<?>[] ps = m.getParameterTypes();
                    for (int i = 0; i < ps.length; i++) {
                        if (i > 0) sb.append(", ");
                        sb.append(ps[i].getSimpleName());
                    }
                    sb.append(") -> ").append(m.getReturnType().getSimpleName());
                    sb.append((m.getModifiers() & java.lang.reflect.Modifier.STATIC) != 0
                            ? " [static]" : "");
                    rec(sb.toString());
                }
            } catch (Throwable t) {
                rec("W34 list methods FAIL: " + t.getClass().getSimpleName());
            }

            Method open = null;
            try {
                open = aph.getDeclaredMethod("open", String.class);
            } catch (NoSuchMethodException e) {
                rec("W34 open(String) 不存在，改用 GURL（v1.18）");
            }
            if (open != null) open.setAccessible(true);

            // v1.18：真实签名是 open(GURL) / getMimeType(InputStream, GURL)
            Class<?> gurlClass;
            java.lang.reflect.Constructor<?> gurlCtor = null;
            Method openGurl = null;
            Method getMimeGurl = null;
            try {
                gurlClass = Class.forName("org.chromium.url.GURL", false, cl);
                gurlCtor = gurlClass.getConstructor(String.class);
                openGurl = aph.getDeclaredMethod("open", gurlClass);
                getMimeGurl = aph.getDeclaredMethod("getMimeType", InputStream.class, gurlClass);
                openGurl.setAccessible(true);
                getMimeGurl.setAccessible(true);
                rec("W34 GURL 路径就绪: " + gurlClass.getName());
            } catch (Throwable t) {
                rec("W34 GURL 反射失败: " + t.getClass().getSimpleName() + ": " + t.getMessage());
            }

            if (openGurl != null && gurlCtor != null) {
                probeOneProtocolUrl("沙箱 html", sandboxUrl, gurlCtor, openGurl, getMimeGurl);
                probeOneProtocolUrl("/data/local/tmp html", tmpUrl, gurlCtor, openGurl, getMimeGurl);
            } else {
                rec("W34 skip 实测：GURL 反射不可用");
            }
        } catch (Throwable t) {
            rec("W34 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    /** W34 的单次探测：open(GURL) + getMimeType(InputStream, GURL) */
    private void probeOneProtocolUrl(String label, String url,
                                     java.lang.reflect.Constructor<?> gurlCtor,
                                     Method openGurl, Method getMimeGurl) {
        if (url == null) {
            rec("W34 [" + label + "] skip: url=null");
            return;
        }
        try {
            Object gurl = gurlCtor.newInstance(url);
            Object stream;
            try {
                stream = openGurl.invoke(null, gurl);
            } catch (Throwable t) {
                Throwable cause = (t instanceof java.lang.reflect.InvocationTargetException
                        && t.getCause() != null) ? t.getCause() : t;
                rec("W34 [" + label + "] open THREW "
                        + cause.getClass().getSimpleName() + ": " + cause.getMessage());
                return;
            }
            rec("W34 [" + label + "] open = "
                    + (stream == null ? "null" : stream.getClass().getName()));
            if (stream == null) {
                rec("W34 [" + label + "] 无流，getMimeType 跳过");
                return;
            }
            try {
                rec("W34 [" + label + "] getMimeType = "
                        + getMimeGurl.invoke(null, stream, gurl));
            } catch (Throwable t) {
                Throwable cause = (t instanceof java.lang.reflect.InvocationTargetException
                        && t.getCause() != null) ? t.getCause() : t;
                rec("W34 [" + label + "] getMimeType THREW "
                        + cause.getClass().getSimpleName() + ": " + cause.getMessage());
            }
        } catch (Throwable t) {
            rec("W34 [" + label + "] FAIL: " + t.getClass().getSimpleName()
                    + ": " + t.getMessage());
        }
    }

    /**
     * W36：确认「WebView 视角的 dataDir」与 W2 的文件路径是否同源。
     *
     * 假设（待验证）：WebView 对「落在自己 dataDir 下的 file://」走特殊分支（不按扩展名），
     * 对「外部路径」走标准 FileURLLoader（按扩展名）。这能同时解释：
     *   W2(沙箱，在 dataDir 下)=text/plain；W14(/data/local/tmp，不在)=text/html；
     *   Gaia 给 WebView 的 dataDir 是 guest 官方路径，与真实文件位置不一致 -> 反而正常。
     *
     * 若 W36 显示 url startsWith(dataDir)=true，则「改 dataDir」这条修法有依据：
     * URL 仍是真实路径 -> 读取不受影响；只是不再落在 dataDir 下 -> 走标准分支。
     */
    private void probeDataDir(String candidateUrlPath) {
        try {
            reportDirPaths("W36 this", this);
            try {
                Class<?> ag = Class.forName("android.app.AppGlobals");
                Object app = ag.getMethod("getInitialApplication").invoke(null);
                if (app instanceof Context) {
                    reportDirPaths("W36 AppGlobals", (Context) app);
                }
                if (app != null) {
                    java.lang.reflect.Field lf = findFieldUp(app.getClass(), "mLoadedApk");
                    if (lf != null) {
                        lf.setAccessible(true);
                        Object la = lf.get(app);
                        if (la != null) {
                            rec("W36 LoadedApk class = " + la.getClass().getName());
                            java.lang.reflect.Field df = findFieldUp(la.getClass(), "mDataDir");
                            if (df != null) {
                                df.setAccessible(true);
                                rec("W36 LoadedApk.mDataDir = " + df.get(la));
                            }
                            java.lang.reflect.Field af =
                                    findFieldUp(la.getClass(), "mApplicationInfo");
                            if (af != null) {
                                af.setAccessible(true);
                                Object ai = af.get(la);
                                if (ai instanceof android.content.pm.ApplicationInfo) {
                                    rec("W36 LoadedApk.mApplicationInfo.dataDir = "
                                            + ((android.content.pm.ApplicationInfo) ai).dataDir);
                                    rec("W36 LoadedApk.mApplicationInfo.packageName = "
                                            + ((android.content.pm.ApplicationInfo) ai).packageName);
                                }
                            }
                        }
                    }
                }
            } catch (Throwable t) {
                rec("W36 AppGlobals 部分失败: " + t.getClass().getSimpleName()
                        + ": " + t.getMessage());
            }
            if (candidateUrlPath != null) {
                String myDataDir = getApplicationInfo().dataDir;
                rec("W36 W2 url path = " + candidateUrlPath);
                rec("W36 this.dataDir = " + myDataDir);
                rec("W36 url startsWith(this.dataDir) = "
                        + (myDataDir != null && candidateUrlPath.startsWith(myDataDir)));
                rec("W36 url startsWith(/data/user/0/com.nextvm.app/) = "
                        + candidateUrlPath.startsWith("/data/user/0/com.nextvm.app/"));
                rec("W36 url startsWith(/data/local/tmp/) = "
                        + candidateUrlPath.startsWith("/data/local/tmp/"));
            }
        } catch (Throwable t) {
            rec("W36 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    private void reportDirPaths(String tag, Context c) {
        try {
            rec(tag + ".getPackageName = " + c.getPackageName());
            rec(tag + ".getApplicationInfo().dataDir = " + c.getApplicationInfo().dataDir);
            rec(tag + ".getDataDir = " + c.getDataDir());
            rec(tag + ".getFilesDir = " + c.getFilesDir());
        } catch (Throwable t) {
            rec(tag + " 读路径失败: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    private java.lang.reflect.Field findFieldUp(Class<?> cls, String name) {
        Class<?> c = cls;
        while (c != null) {
            try {
                return c.getDeclaredField(name);
            } catch (NoSuchFieldException e) {
                c = c.getSuperclass();
            }
        }
        return null;
    }

    /**
     * W37：假 dataDir 的「生效性」与「可用性」。
     *
     * 背景：v139 把 LoadedApk.mApplicationInfo.dataDir 改成 guest 官方路径
     * (/data/user/0/<guest>)，但 VirtualContext.getApplicationInfo() 会
     * `super.getApplicationInfo().apply { dataDir = appDir }` —— 返回的是同一个对象、
     * 原地改回真实沙箱。所以：
     *   ① W37 看到 dataDir 仍是沙箱 -> v139 被 VirtualContext 覆盖（需改 VirtualContext）
     *   ② 看到 /data/user/0/com.sandbox.probe -> v139 生效
     * 同时用 dataDir 拼路径做一次 Java 层读写，评估「framework 内部按 dataDir 读文件」是否失败。
     */
    private void probeDataDirUsability() {
        try {
            String dataDir = getApplicationInfo().dataDir;
            rec("W37 getApplicationInfo().dataDir = " + dataDir);
            rec("W37 仍是沙箱路径（说明被 VirtualContext 覆盖）= "
                    + (dataDir != null && dataDir.startsWith("/data/user/0/com.nextvm.app/")));
            rec("W37 是 guest 官方路径（说明 v139 生效）= "
                    + (dataDir != null && dataDir.startsWith("/data/user/0/com.sandbox.probe")));
            rec("W37 exists(dataDir) = "
                    + (dataDir != null && new File(dataDir).exists()));

            File probe = dataDir == null ? null
                    : new File(dataDir, "wvprobe_datadir_probe.txt");
            if (probe != null) {
                try {
                    FileOutputStream o = new FileOutputStream(probe, false);
                    o.write("ok".getBytes("UTF-8"));
                    o.close();
                    rec("W37 Java 写 via dataDir OK -> " + probe.getAbsolutePath());
                } catch (Throwable t) {
                    rec("W37 Java 写 via dataDir FAIL: " + t.getClass().getSimpleName()
                            + ": " + t.getMessage());
                }
            }

            File g = new File(getFilesDir(), "wvprobe_filesdir_probe.txt");
            try {
                FileOutputStream o = new FileOutputStream(g, false);
                o.write("ok".getBytes("UTF-8"));
                o.close();
                rec("W37 Java 写 via getFilesDir OK");
            } catch (Throwable t) {
                rec("W37 Java 写 via getFilesDir FAIL: " + t.getClass().getSimpleName()
                        + ": " + t.getMessage());
            }
        } catch (Throwable t) {
            rec("W37 FAIL: " + t.getClass().getSimpleName() + ": " + t.getMessage());
        }
    }

    /**
     * W38：Java 层文件 I/O 重定向是否生效（容器 v141 的验收点）。
     *
     * 容器侧用 RegisterNatives 替换了 `libcore.io.Linux.open/access`，
     * 让 Java 层的 File/FileInputStream 也走路径重定向。判据：
     *
     *   容器 translate=false（空转，默认）：写 /sdcard 仍 EPERM，
     *       但 `logcat -s NextVM-Native` 里应出现 `JavaIo open[nofix]: /sdcard/...`
     *   容器 translate=true：写 /sdcard 成功（被翻译到沙箱 sdcard）
     */
    private void probeJavaIoRedirect() {
        final String payload = "W38_JAVAIO_OK";
        File f = new File("/sdcard/wvprobe_javaio.txt");
        try {
            FileOutputStream o = new FileOutputStream(f);
            o.write(payload.getBytes("UTF-8"));
            o.close();
            rec("W38 Java 写 /sdcard OK -> " + f.getAbsolutePath());
        } catch (Throwable t) {
            rec("W38 Java 写 /sdcard FAIL: " + t.getClass().getSimpleName()
                    + ": " + t.getMessage());
        }
        try {
            FileInputStream i = new FileInputStream(f);
            byte[] buf = new byte[64];
            int n = i.read(buf);
            i.close();
            rec("W38 Java 读回 OK: "
                    + (n > 0 ? new String(buf, 0, n, "UTF-8") : "<empty>"));
        } catch (Throwable t) {
            rec("W38 Java 读回 FAIL: " + t.getClass().getSimpleName()
                    + ": " + t.getMessage());
        }
        rec("W38 Java exists(/sdcard/wvprobe_javaio.txt) = " + f.exists());

        // 对照：写 getFilesDir（VirtualContext 给的沙箱路径，一直可用）
        try {
            File g = new File(getFilesDir(), "wvprobe_javaio_control.txt");
            FileOutputStream o = new FileOutputStream(g);
            o.write(payload.getBytes("UTF-8"));
            o.close();
            rec("W38 对照 getFilesDir 写入 OK");
        } catch (Throwable t) {
            rec("W38 对照 getFilesDir 写入 FAIL: " + t.getClass().getSimpleName()
                    + ": " + t.getMessage());
        }
    }

    private static String brief(String s) {
        if (s == null) return "null";
        s = s.replace('\n', ' ');
        s = s.replace("\u003C", "<");
        // v1.12：上限从 160 提到 300 —— onPageFinished 现在返回 JSON（contentType + body），
        //        160 会把 contentType 之后的 body 截掉，看不到"是源码还是渲染结果"
        return s.length() <= 300 ? s : s.substring(0, 300) + "...";
    }
}
