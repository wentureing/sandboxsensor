#!/usr/bin/env python3
"""从 logcat 文本里提取所有完整的 REPORT / EARLY 分块序列并还原为 JSON。

用法: python tools/extract-report.py <logcat.txt>
输出: 与日志同目录的 report-<name>-<n>.json / early-<name>-<n>.json
"""
import base64
import re
import sys
from pathlib import Path


def extract(text, tag, log_path):
    pat = re.compile(rf"{tag} v1 part (\d+)/(\d+) (\S+)")
    pending = {}
    total = None
    count = 0
    for line in text.splitlines():
        m = pat.search(line)
        if not m:
            continue
        idx, n, b64 = int(m.group(1)), int(m.group(2)), m.group(3)
        if total != n:
            pending, total = {}, n
        pending[idx] = b64
        if total and len(pending) == total:
            try:
                raw = base64.b64decode("".join(pending[k] for k in range(1, total + 1)))
            except Exception as e:  # noqa: BLE001
                print(f"  {tag}: decode failed ({e})")
                pending, total = {}, None
                continue
            count += 1
            out = log_path.parent / f"{tag.lower()}-{log_path.stem}-{count}.json"
            out.write_bytes(raw)
            print(f"  {out.name}  {len(raw)} bytes  parts={total}")
            pending, total = {}, None
    if count == 0:
        print(f"  {tag}: 无完整序列")
    return count


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    log = Path(sys.argv[1])
    text = log.read_text(encoding="utf-8", errors="replace")
    n1 = extract(text, "REPORT", log)
    n2 = extract(text, "EARLY", log)
    print(f"done: REPORT={n1} EARLY={n2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
