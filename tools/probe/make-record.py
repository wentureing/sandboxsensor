#!/usr/bin/env python3
"""把探针报告转成可读的 Markdown 记录，便于归档与 T1/T2 对照。

用法:
    python tools/probe/make-record.py probe-reports/report-installed-XXXX.json
输出:
    records/<mode>-<model>-<stamp>.md
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "records"

GROUP_TITLES = {
    "provider": "Provider 阶段快照（承接时机）",
    "context": "Context 目录",
    "external": "外部存储 / Environment",
    "appinfo": "ApplicationInfo",
    "pm": "PackageManager",
    "identity": "身份（UID / PID / Build / SELinux）",
    "literal": "字面量路径（L1 关键用例）",
    "canonical": "canonicalPath",
    "javaio": "java.io / SharedPreferences",
    "device": "设备",
    "native": "libc / native",
}


def shorten(v, n=140):
    s = str(v).replace("\n", " ").replace("|", "\\|")
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    data = json.loads(src.read_text(encoding="utf-8"))

    pkg = data.get("packageName", "?")
    mode = data.get("mode", "?")
    entries = []
    for key in ("provider", "entries"):
        entries.extend(data.get(key, []))

    timeline = data.get("timeline", [])
    t0 = timeline[0]["uptimeMs"] if timeline else 0

    # 设备信息取自同目录的 device-*.txt（collect-probe.ps1 生成）
    device_lines = []
    for cand in sorted(src.parent.glob("device-*.txt")):
        if cand.stat().st_mtime <= src.stat().st_mtime + 5:
            device_lines = cand.read_text(encoding="utf-8").splitlines()
    device = {}
    for line in device_lines:
        if "=" in line:
            k, v = line.split("=", 1)
            device[k.strip()] = v.strip()

    model = device.get("ro.product.model", "device")
    stamp = src.stem.split("-")[-1]
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"{mode.replace(':', '-')}-{model}-{stamp}.md"

    L = []
    L.append(f"# 探针记录：{mode} @ {model}\n")
    L.append(f"- 源报告：`{src.as_posix()}`")
    L.append(f"- 包名：`{pkg}`")
    L.append(f"- 条目：**{len(entries)}**\n")

    if device:
        L.append("## 设备\n")
        L.append("| 属性 | 值 |")
        L.append("| --- | --- |")
        for k in (
            "ro.build.version.release",
            "ro.build.version.sdk",
            "ro.build.version.security_patch",
            "ro.product.model",
            "ro.product.manufacturer",
            "ro.product.cpu.abi",
            "PAGESIZE",
            "ro.build.fingerprint",
        ):
            if k in device:
                L.append(f"| `{k}` | {device[k]} |")
        L.append("")

    if timeline:
        L.append("## 启动时序（相对首个标记）\n")
        L.append("| # | phase | +ms | pid | process |")
        L.append("| ---: | --- | ---: | ---: | --- |")
        for i, t in enumerate(timeline, 1):
            L.append(
                f"| {i} | `{t.get('phase')}` | {t.get('uptimeMs', 0) - t0} | "
                f"{t.get('pid')} | {t.get('process')} |"
            )
        L.append("")
        phases = [t.get("phase") for t in timeline]
        if "ContentProvider.onCreate" in phases and "Application.onCreate" in phases:
            prov = phases.index("ContentProvider.onCreate")
            app = phases.index("Application.onCreate")
            verdict = "✅ Provider 早于 Application.onCreate" if prov < app else "❌ 顺序异常"
            L.append(f"> **承接时机实证**：{verdict}（{phases[prov]} → {phases[app]}）\n")

    # 按 group 分组输出
    order = list(GROUP_TITLES.keys())
    grouped = {}
    for e in entries:
        grouped.setdefault(e.get("group", "other"), []).append(e)

    for g in order + [x for x in grouped if x not in order]:
        if g not in grouped:
            continue
        L.append(f"## {GROUP_TITLES.get(g, g)}\n")
        L.append("| id | 名称 | 状态 | 值 |")
        L.append("| --- | --- | --- | --- |")
        for e in grouped[g]:
            st = e.get("status", "?")
            val = e.get("value") if st == "ok" else e.get("error")
            L.append(
                f"| `{e.get('id')}` | {shorten(e.get('name'), 40)} | {st} | `{shorten(val)}` |"
            )
        L.append("")

    fails = [e for e in entries if e.get("status") != "ok"]
    L.append("## 结论\n")
    L.append(f"- 条目总数 **{len(entries)}**，失败 **{len(fails)}**")
    if fails:
        for e in fails:
            L.append(f"- ❌ `{e.get('id')}` {e.get('name')}: {shorten(e.get('error'), 80)}")
    else:
        L.append("- 全部通过（作为 golden 基线使用）")
    L.append("")

    out.write_text("\n".join(L), encoding="utf-8")
    print(f"written: {out}")
    print(f"entries={len(entries)} fails={len(fails)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
