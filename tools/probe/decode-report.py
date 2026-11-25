"""从 logcat 抓 PROBE REPORT 的 base64 分段，拼接解码，打印 F09-F14 与其他关键项。"""
import base64
import json
import re
import subprocess
import sys

# 抓 logcat
r = subprocess.run(
    ["adb", "logcat", "-d", "-b", "all", "-s", "PROBE"],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
lines = r.stdout.splitlines()

parts = {}
for line in lines:
    m = re.search(r"REPORT v1 part (\d+)/(\d+)\s+(\S+)", line)
    if m:
        idx = int(m.group(1))
        b64 = m.group(3)
        parts[idx] = b64

if not parts:
    print("没找到 REPORT 分段")
    sys.exit(1)

total = max(parts)
print("收到 %d/%d 段" % (len(parts), total))
missing = [i for i in range(1, total + 1) if i not in parts]
if missing:
    print("缺失段:", missing)
    sys.exit(1)

blob = "".join(parts[i] for i in range(1, total + 1))
try:
    raw = base64.b64decode(blob)
except Exception as e:
    print("base64 解码失败:", e)
    sys.exit(1)

try:
    data = json.loads(raw.decode("utf-8", "replace"))
except Exception as e:
    print("JSON 解析失败:", e)
    print(raw[:400])
    sys.exit(1)

print("schema=%s mode=%s package=%s" % (data.get("schema"), data.get("mode"), data.get("packageName")))
print()

entries = data.get("entries") or []
print("共 %d 条" % len(entries))
print()

names = {e.get("id"): e for e in entries}
ORDER = ["F01","F02","F03","F04","F05","F06","F07","F08",
         "F09","F10","F11","F12","F13","F14"]

print("=" * 70)
print("=== F 系列（文件系统）===")
print("=" * 70)
for k in ORDER:
    e = names.get(k)
    if not e:
        print("  %-5s (缺失)" % k)
        continue
    if e.get("status") == "skipped":
        print("  %-5s (本轮暂停：不在选中的 suite 内)" % k)
        continue
    print("  %-5s %s" % (k, e.get("name")))
    print("        [%s] %s" % (e.get("status"), e.get("value")))

print()
print("=" * 70)
print("=== 关键上下文项（路径来源）===")
print("=" * 70)
for k in ["J08","J09","J10","J11","J12","J13","J29","J30","J32","J34"]:
    e = names.get(k)
    if e:
        print("  %-5s %-32s %s" % (k, e.get("name"), e.get("value")))

print()
print("=" * 70)
print("=== 非 ok 的项 ===")
print("=" * 70)
bad = [e for e in entries if e.get("status") != "ok"]
if bad:
    for e in bad:
        print("  %-6s %-32s [%s] %s" % (e.get("id"), e.get("name"), e.get("status"), e.get("value")))
else:
    print("  （全部 ok）")
