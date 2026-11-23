#!/usr/bin/env python3
"""把多份探针报告排成一张矩阵表，方便横向比较不同的虚拟环境。

用法:
    python tools/probe/probe-matrix.py 报告1.json 报告2.json [...] [-o matrix.md]

报告来源优先顺序:
    1) probe-reports/report-*.json      （collect-probe.ps1 / extract-report.py 产出）
    2) 设备上的 files/probe-report.json （run-as 拉取）

每列 = 一个环境（列名默认取报告里的 mode，可用 --name 覆盖）。
输出 markdown 表格，并在末尾列出「环境间取值不同」的项。
"""
import argparse
import json
import os
import sys
from pathlib import Path


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def index_entries(report):
    """id -> (group, name, value, status)；同 id 出现多次时保留第一条并标注。"""
    out = {}
    dup = set()
    for e in report.get("entries") or []:
        eid = e.get("id") or "?"
        if eid in out:
            dup.add(eid)
            continue
        out[eid] = (
            e.get("group", ""),
            e.get("name", ""),
            str(e.get("value", "")),
            e.get("status", ""),
        )
    return out, dup


def short(v, limit):
    v = v.replace("\n", " ").replace("|", "\\|").strip()
    if len(v) > limit:
        v = v[: limit - 1] + "…"
    return v or "(empty)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reports", nargs="+", help="json 报告路径")
    ap.add_argument("-o", "--out", help="输出 markdown 文件（默认打印到 stdout）")
    ap.add_argument("--name", action="append", default=None,
                    help="列名，可重复；不传则用报告的 mode 字段")
    ap.add_argument("--width", type=int, default=48, help="单元格最大宽度")
    ap.add_argument("--only-diff", action="store_true", help="只输出环境间有差异的项")
    args = ap.parse_args()

    cols = []
    for p in args.reports:
        rep = load(p)
        entries, dup = index_entries(rep)
        mode = rep.get("mode", "?")
        cols.append({
            "file": os.path.basename(p),
            "label": (args.name[len(cols)] if args.name and len(args.name) > len(cols) else mode),
            "entries": entries,
            "dup": dup,
            "pkg": rep.get("packageName", "?"),
        })

    # 所有出现过的 id，保持稳定顺序（先按首次出现）
    seen = []
    seen_set = set()
    for c in cols:
        for eid in c["entries"]:
            if eid not in seen_set:
                seen_set.add(eid)
                seen.append(eid)

    rows = []
    for eid in seen:
        vals = []
        group = name = ""
        for c in cols:
            e = c["entries"].get(eid)
            if e is None:
                vals.append("(absent)")
                continue
            group = group or e[0]
            name = name or e[1]
            vals.append(e[2])
        uniq = set(vals)
        differ = len(uniq) > 1
        if args.only_diff and not differ:
            continue
        rows.append((eid, group, name, vals, differ))

    lines = []
    lines.append("| ID | 大类 | 检测项 | " + " | ".join(c["label"] for c in cols) + " | 差异 |")
    lines.append("| --- | --- | --- | " + " | ".join("---" for _ in cols) + " | --- |")
    for eid, group, name, vals, differ in rows:
        mark = "**●**" if differ else ""
        cells = " | ".join(short(v, args.width) for v in vals)
        lines.append(f"| `{eid}` | {group} | {short(name, 40)} | {cells} | {mark} |")

    n_diff = sum(1 for r in rows if r[4])
    lines.append("")
    lines.append(f"共 {len(rows)} 项，其中 **{n_diff} 项在环境间取值不同**。")
    lines.append("")
    lines.append("环境说明：")
    for c in cols:
        dup = ("，重复id=" + ",".join(sorted(c["dup"]))) if c["dup"] else ""
        lines.append(f"- **{c['label']}** — `{c['file']}`，packageName=`{c['pkg']}`{dup}")

    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"written: {args.out}  ({len(rows)} rows, {n_diff} differ)")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
