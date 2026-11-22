import json, glob, os

ROOT = r"C:\Users\86177\Desktop\轻简虚拟环境开发\probe-reports"
files = sorted(glob.glob(os.path.join(ROOT, "report-run-192038-*.json")),
               key=lambda p: int(p.rsplit("-", 1)[1].split(".")[0]))

print("%-4s %-42s %-6s %s" % ("#", "mode", "n", "packageName"))
print("-" * 100)
for p in files:
    d = json.load(open(p, encoding="utf-8"))
    n = len(d.get("entries") or [])
    idx = p.rsplit("-", 1)[1].split(".")[0]
    print("%-4s %-42s %-6d %s" % (idx, str(d.get("mode"))[:42], n, d.get("packageName")))

print()
print("=== 容器报告的关键项（mode 含 container）===")
KEYS = ("P02", "P03", "P05", "P06", "P07", "P09", "P11", "P12", "P13", "P14",
        "P15", "P16", "P17", "P21", "P23", "J01", "J14", "J16", "J19", "J22")
for p in files:
    d = json.load(open(p, encoding="utf-8"))
    if "container" not in str(d.get("mode")):
        continue
    ents = {e["id"]: e for e in d.get("entries") or []}
    print("--- %s  mode=%s ---" % (os.path.basename(p), d.get("mode")))
    for k in KEYS:
        e = ents.get(k)
        if e:
            print("  %-4s %-30s %s" % (k, str(e.get("name", ""))[:28], str(e.get("value", ""))[:100]))
    print()
