import io, os, re, glob, json

ROOT = r"C:\Users\86177\Desktop\轻简虚拟环境开发\_ref\ShitJumpingVM"

# 核心类 -> 定义文件（相对路径）
CORE = {
    "VirtualEngine": r"core\virtualization\src\main\java\com\nextvm\core\virtualization\engine\VirtualEngine.kt",
    "VirtualApplicationManager": r"core\virtualization\src\main\java\com\nextvm\core\virtualization\lifecycle\VirtualApplicationManager.kt",
    "ActivityThreadHook": r"core\virtualization\src\main\java\com\nextvm\core\virtualization\engine\ActivityThreadHook.kt",
    "VirtualResourceManager": r"core\virtualization\src\main\java\com\nextvm\core\virtualization\lifecycle\VirtualResourceManager.kt",
    "VirtualMultiProcessManager": r"core\virtualization\src\main\java\com\nextvm\core\virtualization\process\VirtualMultiProcessManager.kt",
    "VirtualWindowManager": r"core\virtualization\src\main\java\com\nextvm\core\virtualization\ui\VirtualWindowManager.kt",
    "StubRegistry": r"core\virtualization\src\main\java\com\nextvm\core\virtualization\engine\StubRegistry.kt",
    "SystemServiceProxyManager": r"core\services\src\main\java\com\nextvm\core\services\SystemServiceProxyManager.kt",
    "ActivityManagerProxy": r"core\binder\src\main\java\com\nextvm\core\binder\ActivityManagerProxy.kt",
    "PackageManagerProxy": r"core\binder\src\main\java\com\nextvm\core\binder\PackageManagerProxy.kt",
    "NativeHookBridge": r"core\hook\src\main\java\com\nextvm\core\hook\NativeHookBridge.kt",
    "SandboxManager": r"core\sandbox\src\main\java\com\nextvm\core\sandbox\SandboxManager.kt",
}

# 找出每种核心类实际所在的文件（有些名字不在上面的表里）
def find_file(simple):
    hits = []
    for p in glob.glob(ROOT + r"\**\*.kt", recursive=True):
        if r"\build\\" in p:
            continue
        try:
            t = io.open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        if re.search(r"^(class|object|interface) " + re.escape(simple) + r"\b", t, re.M):
            hits.append(p)
    return hits[0] if hits else None

sources = {}
for name, rel in CORE.items():
    p = os.path.join(ROOT, rel)
    if os.path.exists(p):
        sources[name] = p
    else:
        alt = find_file(name)
        if alt:
            sources[name] = alt

print("=== 数据采集 ===")
info = {}
for name, p in sources.items():
    lines = io.open(p, encoding="utf-8", errors="replace").read().splitlines()
    # 类定义起始行
    start = 0
    for i, l in enumerate(lines):
        if re.match(r"^(class|object|interface) " + re.escape(name) + r"\b", l):
            start = i
            break
    body = lines[start:]
    funs = sum(1 for l in body if re.match(r"^\s{4}(suspend |inline |private |internal |override )*fun ", l))
    classes = sum(1 for l in body if re.match(r"^(class|object|interface|data class|enum class) ", l))
    info[name] = {"file": os.path.basename(p), "lines": len(body), "funs": funs, "classes": classes}
    print("  %-30s %-34s %5d 行  %3d 函数  %d 类" % (name, os.path.basename(p), len(body), funs, classes))

# 依赖：A 的全文里出现 B
print()
print("=== 依赖矩阵（行=使用者，列=被依赖）===")
names = list(info.keys())
texts = {n: io.open(sources[n], encoding="utf-8", errors="replace").read() for n in names}
deps = {}
for a in names:
    for b in names:
        if a == b:
            continue
        # 用 \b 匹配类名
        cnt = len(re.findall(r"\b" + re.escape(b) + r"\b", texts[a]))
        if cnt > 0:
            deps.setdefault(a, {})[b] = cnt
for a in names:
    d = deps.get(a, {})
    if d:
        print("  %-28s -> %s" % (a, ", ".join("%s(%d)" % (k, v) for k, v in sorted(d.items(), key=lambda kv: -kv[1]))))

json.dump({"info": info, "deps": deps}, io.open(os.path.join(ROOT, "_graph.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print()
print("saved _graph.json")
