"""统计 via 对 android.webkit.* 的所有 API 用法，定位它如何驱动 WebView。

背景：loadUrl / loadDataWithBaseURL / shouldInterceptRequest 都是 0 次，
但 WebResourceResponse 有 71 次 —— 说明 via 有一套自己的内容提供机制，
需要看它到底调了哪些 webkit API。
"""
import os
import re
from collections import Counter, defaultdict

OUT = "dist/viadex/dex-dump.txt"
if not os.path.exists(OUT):
    raise SystemExit("先跑 tools/audit/decompile-via-webview.py 生成 dump")

# 统计：类 -> 方法 -> 次数
api = defaultdict(Counter)
# WebResourceResponse 的构造点（谁在造它）
wrr_callers = []
# WebViewClient 子类里 override 的方法
client_methods = []

cur_class = None
cur_method = None

CALL_RE = re.compile(r"(?:invoke-\w+(?:/range)?)\s+\{[^}]*\},\s*(Landroid/webkit/[^;]+);->([\w<>$]+)")

with open(OUT, "r", encoding="utf-8", errors="replace") as f:
    for lineno, line in enumerate(f, 1):
        m = re.match(r"\s*Class descriptor\s*:\s*'([^']+)'", line)
        if m:
            cur_class = m.group(1)
            cur_method = None
            continue
        m = re.match(r"\s*name\s*:\s*'([^']+)'", line)
        if m and cur_class:
            cur_method = m.group(1)
            continue

        cm = CALL_RE.search(line)
        if cm:
            klass, meth = cm.group(1), cm.group(2)
            api[klass][meth] += 1
            if klass == "Landroid/webkit/WebResourceResponse" and meth == "<init>":
                wrr_callers.append((cur_class, cur_method, lineno))

print("=" * 78)
print("A. via 用到的 android.webkit.* 类与 API（按调用次数）")
print("=" * 78)
for klass in sorted(api, key=lambda k: -sum(api[k].values())):
    total = sum(api[klass].values())
    print(f"\n  {klass}   总 {total} 次")
    for meth, n in api[klass].most_common(12):
        print(f"      {meth:46} {n:>4}")

print()
print("=" * 78)
print("B. WebResourceResponse 的构造点（via 在哪里「造内容」）")
print("=" * 78)
seen = set()
for cls, meth, ln in wrr_callers:
    key = (cls, meth)
    if key in seen:
        continue
    seen.add(key)
    print(f"  L{ln:<8} {cls}  ::  {meth}")
print(f"  共 {len(wrr_callers)} 处，去重后 {len(seen)} 个方法")

print()
print("=" * 78)
print("C. 这些类里，哪些是 WebViewClient / WebChromeClient 的子类")
print("=" * 78)
# 找 superclass 是 WebViewClient 的类
with open(OUT, "r", encoding="utf-8", errors="replace") as f:
    text = f.read()

for m in re.finditer(r"Class descriptor\s*:\s*'([^']+)'(.{0,600}?)Superclass\s*:\s*'([^']+)'",
                     text, re.S):
    cls, _, sup = m.group(1), m.group(2), m.group(3)
    if "webkit" in sup:
        print(f"  {cls}  extends  {sup}")

print()
print("=" * 78)
print("D. 判断：via 是否把 file:// 读文件的工作交给了自己的代码")
print("=" * 78)
has_wrr = sum(api.get("Landroid/webkit/WebResourceResponse", {}).values())
has_loadurl = api.get("Landroid/webkit/WebView", {}).get("loadUrl", 0)
print(f"  WebResourceResponse 构造/使用: {has_wrr} 次")
print(f"  WebView.loadUrl:              {has_loadurl} 次")
if has_wrr > 0 and has_loadurl == 0:
    print("  => via 很可能通过 shouldInterceptRequest 之类自行产出内容，")
    print("     而不是让 WebView 直接去读 file://。若如此，转换点不在 loadUrl，")
    print("     而在「它调用哪个 API 去加载（可能是 loadData / evaluateJavascript / 内部封装）」。")
