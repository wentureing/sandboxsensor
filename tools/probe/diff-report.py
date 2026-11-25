#!/usr/bin/env python3
"""对比两份探针报告（T1 直接安装 vs T2 容器内），输出差异表。

用法:
    python tools/probe/diff-report.py probe-reports/report-installed-*.json \
                                 probe-reports/report-container-*.json
"""
import json
import sys
from pathlib import Path


def load(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = {}
    for group_key in ("provider", "entries"):
        for e in data.get(group_key, []):
            entries[e["id"]] = e
    timeline = {t["phase"]: t for t in data.get("timeline", [])}
    return data, entries, timeline


def fmt(e):
    if e is None:
        return "—"
    if e.get("status") == "skipped":
        return "—（暂停）"
    if e.get("status") == "ok":
        return str(e.get("value"))
    return f"FAIL({e.get('error')})"


def shorten(s, n=110):
    s = str(s).replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    a_path, b_path = Path(sys.argv[1]), Path(sys.argv[2])
    a, a_entries, a_tl = load(a_path)
    b, b_entries, b_tl = load(b_path)

    print(f"# 探针报告差异\n")
    print(f"- A（基线）: `{a_path.name}` mode=`{a.get('mode')}`")
    print(f"- B（容器）: `{b_path.name}` mode=`{b.get('mode')}`\n")

    print("## 启动时序\n")
    print("| phase | A uptime | B uptime |")
    print("| --- | ---: | ---: |")
    for phase in sorted(set(a_tl) | set(b_tl)):
        ta = a_tl.get(phase, {}).get("uptimeMs", "—")
        tb = b_tl.get(phase, {}).get("uptimeMs", "—")
        print(f"| {phase} | {ta} | {tb} |")

    rows = []
    for eid in sorted(set(a_entries) | set(b_entries)):
        ea, eb = a_entries.get(eid), b_entries.get(eid)
        va, vb = fmt(ea), fmt(eb)
        if va == vb:
            continue
        group = (ea or eb or {}).get("group", "?")
        name = (ea or eb or {}).get("name", eid)
        rows.append((group, eid, name, shorten(va), shorten(vb)))

    print(f"\n## 差异项：{len(rows)}\n")
    print("| group | id | 名称 | A（直接安装） | B（容器内） |")
    print("| --- | --- | --- | --- | --- |")
    for group, eid, name, va, vb in rows:
        print(f"| {group} | {eid} | {name} | `{va}` | `{vb}` |")

    same = len(set(a_entries) & set(b_entries)) - len(rows)
    print(f"\n一致项：{same}；仅 A 有：{len(set(a_entries) - set(b_entries))}；"
          f"仅 B 有：{len(set(b_entries) - set(a_entries))}")

    print("\n## 关注点判定\n")
    checks = [
        ("J22", "UID 矛盾（myUid vs officialUid）"),
        ("J01", "dataDir 前缀"),
        ("J26", "/data/data/<pkg> 字面量读写"),
        ("J27", "/data/user/0/<pkg> 字面量读写"),
        ("J30", "filesDir canonical"),
        ("N03.open_literal", "native open 字面量"),
        ("N07.openat_relative", "openat 相对路径"),
        ("N08.readlink_fd", "/proc/self/fd 是否泄露"),
        ("N10.maps_scan", "maps 特征"),
    ]
    for eid, desc in checks:
        ea, eb = a_entries.get(eid), b_entries.get(eid)
        status = "一致" if fmt(ea) == fmt(eb) else "**不同**"
        ok_b = (eb or {}).get("status") == "ok"
        print(f"- {eid} {desc}: {status}；容器内 status={'ok' if ok_b else 'fail'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
