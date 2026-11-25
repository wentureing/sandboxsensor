"""验证：多层 hook 的安全性前提 —— 翻译是否幂等（翻译后的路径会不会再被规则匹配）。

如果沙箱路径本身会命中任何一条规则，那么：
  Java 层 hook 翻译一次 -> 内部 libc 调用被 GOT patch 再翻译一次 -> 双重翻译 -> 出错。
所以"多层 hook 只 hook 最外层就够"这个结论，**依赖幂等性**。

本脚本枚举 setupAppRedirections + setupExternalStorageRedirections 注册的全部规则，
对沙箱路径（既含 /data/user/0/com.nextvm.app/files/virtual/... 也含 <sandbox>/sdcard/...）
逐条测试，输出是否会二次命中。
"""
import re

RULES = [
    # setupAppRedirections
    ("/data/data/{guest}/",                    "{sandbox}/"),
    ("/data/user/0/{guest}/",                  "{sandbox}/"),
    ("/storage/emulated/0/Android/data/{guest}/", "{sandbox}/external_data/"),
    ("/sdcard/Android/data/{guest}/",          "{sandbox}/external_data/"),
    ("/storage/emulated/0/Android/obb/{guest}/", "{sandbox}/obb/"),
    # 方案A 宿主兜底
    ("/storage/emulated/0/Android/data/com.nextvm.app/", "{sandbox}/host_ext/"),
    ("/sdcard/Android/data/com.nextvm.app/",   "{sandbox}/host_ext/"),
    # setupExternalStorageRedirections（通用）
    ("/sdcard/",                "{sandbox}/sdcard/"),
    ("/storage/emulated/0/",    "{sandbox}/sdcard/"),
    ("/mnt/sdcard/",            "{sandbox}/sdcard/"),
    ("/storage/self/primary/",  "{sandbox}/sdcard/"),
    ("/storage/emulated/0",     "{sandbox}/sdcard"),
    ("/sdcard",                 "{sandbox}/sdcard"),
]

GUEST = "mark.via"
INSTANCE = "mark.via_1795580392079"
SANDBOX = f"/data/user/0/com.nextvm.app/files/virtual/data/{INSTANCE}"

# 翻译后会出现的几类沙箱路径（都要检查是否会被二次命中）
CANDIDATES = [
    f"{SANDBOX}/",                                   # data 根
    f"{SANDBOX}/files/x.mht",                        # 内部 files
    f"{SANDBOX}/external_data/files/x.mht",          # external_data
    f"{SANDBOX}/sdcard/Android/data/mark.via/files/offline/x.mht",  # 通用 sdcard 映射
    f"{SANDBOX}/host_ext/files/offline/x.mht",       # 方案A 兜底目标
    f"{SANDBOX}/obb/x.obb",
]

def materialize(rule):
    return (rule[0].replace("{guest}", GUEST).replace("{sandbox}", SANDBOX),
            rule[1].replace("{guest}", GUEST).replace("{sandbox}", SANDBOX))

def longest_match(path):
    """模拟 native redirect_path：最长前缀优先。"""
    best_from = best_to = ""
    for f, t in (materialize(r) for r in RULES):
        if path.startswith(f) and len(f) > len(best_from):
            best_from, best_to = f, t
    if not best_from:
        return None
    return best_from, best_to, best_to + path[len(best_from):]

print("=== 规则表（物化后）===")
for f, t in (materialize(r) for r in RULES):
    print(f"  {f:52} -> {t}")

print("\n=== 沙箱路径是否会被二次命中（幂等性检查）===")
bad = 0
for p in CANDIDATES:
    hit = longest_match(p)
    if hit is None:
        print(f"  OK   (不命中)  {p}")
    else:
        bad += 1
        print(f"  RISK (二次命中) {p}")
        print(f"         被规则 {hit[0]} 命中 -> {hit[2]}")

print()
if bad == 0:
    print("结论：所有沙箱路径都不会被任何规则再次命中 -> 翻译幂等")
    print("      => 多层 hook（Java 层 + libc GOT）是安全的，内层不会二次翻译")
else:
    print(f"结论：{bad} 条沙箱路径会被二次命中 -> 多层 hook 有双重翻译风险")

# 额外：容器自身路径是否会被 guest 规则命中（自噬风险）
print("\n=== 自噬检查：容器自身路径会不会被 guest 规则命中 ===")
host_paths = [
    "/data/user/0/com.nextvm.app/files/virtual/apks/x.apk",
    "/data/user/0/com.nextvm.app/files/checks.log",
    "/storage/emulated/0/Android/data/com.nextvm.app/files/offline/x.mht",
]
for p in host_paths:
    hit = longest_match(p)
    if hit:
        print(f"  HIT  {p}")
        print(f"       -> {hit[2]}")
    else:
        print(f"  不命中 {p}")
print("  （注：规则只在 guest 进程注册，容器主进程不装 hook，所以'命中'不代表自噬）")
