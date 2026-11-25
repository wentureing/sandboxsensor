"""反编译 via，找它与 WebView 交互的所有调用点。

关注：
  WebView.loadUrl(String)
  WebView.loadDataWithBaseURL(...)
  WebView.loadData(...)
  WebViewClient.shouldInterceptRequest
  WebSettings 里与 file:// 访问相关的开关
  via 自己拼 file:// 的地方（字符串常量）

目的：确定「假路径 -> 真路径」应该在哪个函数处转换。
"""
import glob
import os
import re
import subprocess

BT = sorted(glob.glob(os.path.expanduser(
    r"~/AppData/Local/Android/Sdk/build-tools/*/dexdump.exe")))[-1]
DEXDIR = "dist/viadex"
OUT = "dist/viadex/dex-dump.txt"

if not os.path.exists(OUT) or os.path.getsize(OUT) < 1000:
    print("生成 dexdump -d 输出（可能较慢）...")
    with open(OUT, "w", encoding="utf-8", errors="replace") as f:
        for dex in sorted(glob.glob(os.path.join(DEXDIR, "*.dex"))):
            print("  dumping", dex)
            subprocess.run([BT, "-d", dex], stdout=f, stderr=subprocess.DEVNULL)
    print(f"  输出 {os.path.getsize(OUT)/1048576:.1f} MB")
else:
    print(f"复用已有 dump：{os.path.getsize(OUT)/1048576:.1f} MB")

print()
print("=" * 78)
print("A. WebView / WebViewClient / WebSettings 的所有引用")
print("=" * 78)

PATTERNS = {
    "WebView.loadUrl": r"Landroid/webkit/WebView;->loadUrl\(",
    "WebView.loadDataWithBaseURL": r"Landroid/webkit/WebView;->loadDataWithBaseURL\(",
    "WebView.loadData": r"Landroid/webkit/WebView;->loadData\(",
    "WebViewClient.shouldInterceptRequest": r"Landroid/webkit/WebViewClient;->shouldInterceptRequest\(",
    "WebViewClient.shouldOverrideUrlLoading": r"Landroid/webkit/WebViewClient;->shouldOverrideUrlLoading\(",
    "WebSettings.setAllowFileAccess": r"Landroid/webkit/WebSettings;->setAllowFileAccess",
    "WebSettings.setAllowFileAccessFromFileURLs": r"Landroid/webkit/WebSettings;->setAllowFileAccessFromFileURLs",
    "WebSettings.setAllowUniversalAccessFromFileURLs": r"Landroid/webkit/WebSettings;->setAllowUniversalAccessFromFileURLs",
    "WebViewAssetLoader": r"Landroidx/webkit/WebViewAssetLoader",
    "WebResourceResponse": r"Landroid/webkit/WebResourceResponse;",
}

counts = {}
with open(OUT, "r", encoding="utf-8", errors="replace") as f:
    lines = f.readlines()

cur_class = None
cur_method = None
hits = {k: [] for k in PATTERNS}

for i, line in enumerate(lines):
    m = re.match(r"\s*Class descriptor\s*:\s*'([^']+)'", line)
    if m:
        cur_class = m.group(1)
        cur_method = None
        continue
    m = re.match(r"\s*name\s*:\s*'([^']+)'", line)
    if m and cur_class:
        cur_method = m.group(1)
        continue
    for name, pat in PATTERNS.items():
        if re.search(pat, line):
            hits[name].append((cur_class, cur_method, i + 1, line.strip()))

for name in PATTERNS:
    n = len(hits[name])
    counts[name] = n
    print(f"  {name:46} {n:>4} 次")

print()
print("=" * 78)
print("B. 具体的调用点（方法级别）")
print("=" * 78)
for name in ["WebView.loadUrl", "WebView.loadDataWithBaseURL", "WebView.loadData",
             "WebViewClient.shouldInterceptRequest", "WebViewClient.shouldOverrideUrlLoading"]:
    if not hits[name]:
        continue
    print(f"\n--- {name} ---")
    seen = set()
    for cls, meth, ln, raw in hits[name]:
        key = (cls, meth)
        if key in seen:
            continue
        seen.add(key)
        print(f"  {cls}  ::  {meth}")

print()
print("=" * 78)
print("C. file:// 相关字符串常量（via 自己拼路径的地方）")
print("=" * 78)
file_strs = set()
for line in lines:
    for m in re.finditer(r"'([^']*file://[^']*)'", line):
        file_strs.add(m.group(1))
    for m in re.finditer(r'"([^"]*file://[^"]*)"', line):
        file_strs.add(m.group(1))
for s in sorted(file_strs)[:30]:
    print(f"  {s}")

print()
print("=" * 78)
print("D. 含 homepage / offline 的字符串（定位 via 主页与离线列表的实现）")
print("=" * 78)
kw = set()
for line in lines:
    for m in re.finditer(r"'([^']{3,120})'", line):
        v = m.group(1)
        if any(k in v.lower() for k in ("homepage", "offline", ".mht", "asset")):
            kw.add(v)
for s in sorted(kw)[:30]:
    print(f"  {s}")

if counts.get("WebView.loadUrl", 0) == 0 and counts.get("WebView.loadDataWithBaseURL", 0) == 0:
    print()
    print("!! 没找到 loadUrl / loadDataWithBaseURL —— 可能 via 用了自定义封装，")
    print("   或 dex-dump 里方法名被混淆。下一步应扩大搜索（如搜 WebView 的所有方法）。")
