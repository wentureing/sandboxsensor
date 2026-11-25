#!/usr/bin/env python3
"""检出「路径转换」相关代码里的重复片段 —— 只出报告，不改代码。

两类检测（用户定义的名义）：
  类型 1  字符串高度相似   —— 归一化后相同的字符串字面量
  类型 2  语法结构/流程极度相似 —— 代码骨架（token 类型序列）相同的代码块

每组按设计理念标注性质，用来判断「该不该合并」：
  数据     纯常量/模板/前缀列表          -> 可以进配置表
  控制流   带副作用的语句/条件/时机      -> 各自保留，不合并
  混合     两者都有                      -> 只合并其中的数据部分

并区分「真重复」与「常量引用」：
  真重复    同一字面量在多处硬编码，且至少有两处不是常量定义
  常量引用  某处定义了常量，别处在引用它（C++ 侧无法引用 Kotlin 常量时才会硬编码）

用法：
    python tools/audit/find-duplicate-fragments.py
    python tools/audit/find-duplicate-fragments.py --group 3
    python tools/audit/find-duplicate-fragments.py --only 真重复
"""
import argparse
import io
import json
import os
import re
import sys
from collections import defaultdict

BASE = "_ref/ShitJumpingVM/"

TARGETS = [
    ("kt", "core/model/src/main/java/com/nextvm/core/model/ProcessSlot.kt"),
    ("kt", "core/sandbox/src/main/java/com/nextvm/core/sandbox/VirtualContext.kt"),
    ("kt", "core/binder/src/main/java/com/nextvm/core/binder/proxy/PackageManagerProxy.kt"),
    ("kt", "core/hook/src/main/java/com/nextvm/core/hook/NativeHookBridge.kt"),
    ("cpp", "core/hook/src/main/cpp/native-hook.cpp"),
]

OUT_MD = "docs/输出/调研/重复片段检出报告.md"
OUT_JSON = "tools/tmp/duplicate-fragments.json"

# 这些是「常量定义值」，出现多次属正常引用，不算重复
KNOWN_CONST_VALUES = {
    "com.greenpoint.android.mc10086.activity",   # CM_PACKAGE
    "com.nextvm.app",                            # HOST_PACKAGE
    "com.nextvm.app.stub",                       # STUB_PREFIX
    "VmK8x2",                                    # SYNTHETIC_INSTALL_TOKEN
    "_nextvm_caller_instance",
    "_nextvm_caller_pkg",
    "arm64",
    "UTF-8",
}


def read(p):
    full = BASE + p
    if not os.path.exists(full):
        return None
    with io.open(full, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n")


STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def norm_string(s):
    t = re.sub(r"\$\{[^}]*\}", "<VAR>", s)
    t = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "<VAR>", t)
    t = re.sub(r"<[A-Za-z_][A-Za-z0-9_ ]*>", "<VAR>", t)
    t = re.sub(r"(<VAR>/)+<VAR>", "<VAR>/<VAR>", t)
    return t


def is_const_definition(line):
    """这一行是不是在定义常量（const val / #define / static const）。"""
    s = line.strip()
    return (
        s.startswith("const val") or s.startswith("private const val")
        or s.startswith("#define")
        or re.match(r"^static\s+const\s", s) is not None
    )


def detect_string_dups(min_len):
    groups = defaultdict(list)
    for lang, path in TARGETS:
        src = read(path)
        if src is None:
            continue
        for lineno, line in enumerate(src.split("\n"), 1):
            s = line.strip()
            if s.startswith("//") or s.startswith("*"):
                continue
            for m in STR_RE.finditer(line):
                raw = m.group(1)
                if len(raw) < min_len:
                    continue
                if raw in KNOWN_CONST_VALUES:
                    continue
                groups[norm_string(raw)].append({
                    "file": path, "line": lineno, "raw": raw,
                    "is_def": is_const_definition(line),
                })
    return {k: v for k, v in groups.items() if len(v) >= 2}


BLOCK_STARTS = [
    re.compile(r"^\s*if\s*\("),
    re.compile(r"^\s*override\s+fun\s+"),
    re.compile(r"^\s*fun\s+"),
    re.compile(r"^\s*static\s+"),
]


def token_skeleton(block_lines):
    toks = []
    key = {
        "if", "else", "when", "return", "fun", "val", "var", "override",
        "private", "public", "static", "const", "for", "while", "break",
        "continue", "null", "true", "false", "new", "delete", "sizeof",
    }
    for line in block_lines:
        s = line.strip()
        if not s or s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        s2 = STR_RE.sub("STR", s)
        s2 = re.sub(r"\b\d+\b", "NUM", s2)
        for p in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|==|!=|<=|>=|&&|\|\||\S", s2):
            if p in key:
                toks.append(p)
            elif re.match(r"^[A-Za-z_]", p):
                toks.append("ID")
            elif p in "(){}[],;=.?:+-/*":
                toks.append(p)
            else:
                toks.append("OP")
    return " ".join(toks)


def detect_block_dups(min_lines):
    groups = defaultdict(list)
    for lang, path in TARGETS:
        src = read(path)
        if src is None:
            continue
        lines = src.split("\n")
        i = 0
        while i < len(lines):
            if not any(r.match(lines[i]) for r in BLOCK_STARTS):
                i += 1
                continue
            depth, started, j = 0, False, i
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    started = True
                if started and depth <= 0:
                    break
                j += 1
            block = lines[i:j + 1]
            if len(block) >= min_lines:
                skel = token_skeleton(block)
                if len(skel) > 60:
                    groups[skel].append({
                        "file": path, "line": i + 1,
                        "head": block[0].strip()[:80],
                        "lines": len(block),
                    })
            i = j + 1
    return {k: v for k, v in groups.items() if len(v) >= 2}


DATA_MARKERS = ["synthetic", "virtualConstants", "/data/", "/sdcard", "/storage",
                "Android/", "files", "cache", "obb", "media"]
CTRL_MARKERS = ["ensureDir", "mkdirs", "addPathRedirection", "isAccessible",
                "runCatching", "Timber", "LOGE", "LOGI", "fetch_add", "throw"]


def classify(text):
    d = any(m in text for m in DATA_MARKERS)
    c = any(m in text for m in CTRL_MARKERS)
    if d and c:
        return "混合"
    if c:
        return "控制流"
    if d:
        return "数据"
    return "未判定"


def real_or_ref(hits):
    """真重复 vs 常量引用。"""
    defs = sum(1 for h in hits if h.get("is_def"))
    n = len(hits)
    if n == 1:
        return "单次"
    if defs >= 1 and n - defs <= 1:
        return "常量引用"
    return "真重复"


ADVICE = {
    "数据": "可考虑并入配置表（纯数据，无副作用）",
    "控制流": "**不要合并**（副作用/时机不同），最多抽辅助函数",
    "混合": "只合并其中的数据部分，控制流保留",
    "未判定": "人工判断",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-len", type=int, default=18)
    ap.add_argument("--min-lines", type=int, default=4)
    ap.add_argument("--group", type=int)
    ap.add_argument("--only", choices=["真重复", "常量引用", "全部"], default="全部")
    args = ap.parse_args()

    str_dups = detect_string_dups(args.min_len)
    blk_dups = detect_block_dups(args.min_lines)

    md = ["# 重复片段检出报告（路径转换相关）", "",
          "> 由 `tools/audit/find-duplicate-fragments.py` 生成。**只出报告，不改代码。**", "",
          "判定类别说明：", "",
          "| 类别 | 含义 | 处置 |", "| --- | --- | --- |",
          "| **真重复** | 同一字面量在多处硬编码，至少两处不是常量定义 | 值得看 |",
          "| **常量引用** | 一处定义常量，别处引用/硬编码 | 一般无需处理 |",
          "| **数据** | 纯模板/前缀 | 可进配置表 |",
          "| **控制流** | 带副作用/时机差异 | **不合并** |", "| **混合** | 两者都有 | 只合并数据部分 |", ""]

    rows = []
    idx = 0

    md.append("## 类型 1：字符串高度相似\n")
    for norm, hits in sorted(str_dups.items(), key=lambda kv: -len(kv[1])):
        idx += 1
        kind = classify(" ".join(h["raw"] for h in hits))
        rel = real_or_ref(hits)
        files = {h["file"] for h in hits}
        cross = "跨文件" if len(files) > 1 else "同文件"
        rows.append({"id": idx, "type": "string", "norm": norm, "count": len(hits),
                     "kind": kind, "real": rel, "cross": cross})
        md.append(f"### #{idx}  {len(hits)} 次 · {rel} · {cross} · 性质={kind}\n")
        md.append(f"- 归一化：`{norm[:120]}`")
        md.append(f"- 建议：{ADVICE[kind]}")
        md.append("")
        for h in hits[:12]:
            tag = " [定义]" if h["is_def"] else ""
            md.append(f"  - `{h['file']}:{h['line']}`{tag}  `{h['raw'][:90]}`")
        if len(hits) > 12:
            md.append(f"  - …另有 {len(hits) - 12} 处")
        md.append("")

    md.append("\n## 类型 2：语法结构 / 流程极度相似\n")
    for skel, hits in sorted(blk_dups.items(), key=lambda kv: -len(kv[1])):
        idx += 1
        kind = classify(" ".join(h["head"] for h in hits))
        files = {h["file"] for h in hits}
        cross = "跨文件" if len(files) > 1 else "同文件"
        rows.append({"id": idx, "type": "structure", "count": len(hits),
                     "kind": kind, "cross": cross})
        md.append(f"### #{idx}  {len(hits)} 次 · {cross} · 性质={kind}\n")
        md.append(f"- 骨架头部：`{skel[:120]}...`")
        md.append(f"- 建议：{ADVICE[kind]}")
        md.append("")
        for h in hits[:10]:
            md.append(f"  - `{h['file']}:{h['line']}`  ({h['lines']} 行)  `{h['head']}`")
        if len(hits) > 10:
            md.append(f"  - …另有 {len(hits) - 10} 处")
        md.append("")

    md.append(f"\n---\n\n共 **{idx}** 组（字符串 {len(str_dups)} + 结构 {len(blk_dups)}）。\n")
    md.append("看图：`python tools/audit/find-duplicate-fragments.py --group <编号>`\n")

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with io.open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"rows": rows,
                   "string": {k: v for k, v in str_dups.items()},
                   "structure": {k: v for k, v in blk_dups.items()}},
                  f, ensure_ascii=False, indent=2)

    # 终端摘要
    print(f"共 {idx} 组（字符串 {len(str_dups)} + 结构 {len(blk_dups)}）")
    print(f"报告: {OUT_MD}")
    print(f"JSON: {OUT_JSON}")
    print()
    print(f"{'#':>4}  {'类型':<8} {'次数':>4}  {'真重复?':<9} {'跨文件?':<7} {'性质':<7}")
    for r in rows:
        if args.only != "全部" and r.get("real") != args.only:
            continue
        if args.group is not None and r["id"] != args.group:
            continue
        print(f"{r['id']:>4}  {r['type']:<8} {r['count']:>4}  "
              f"{r.get('real', '-'):<9} {r.get('cross', '-'):<7} {r['kind']:<7}")


if __name__ == "__main__":
    main()
