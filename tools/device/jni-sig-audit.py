#!/usr/bin/env python3
"""JNI 签名审计：把「设备上真实存在的 native 方法」与「C++ 源码里注册的签名」逐条比对。

## 为什么需要

本项目在 Java 层 hook 上反复翻车，根因都是**签名/类名写错**：
  * v141 起 JavaIoHooks 一直 RegisterNatives 失败，没人发现（批量注册是全或无）
  * opendir 被注册到 libcore.io.Linux，但那个类里根本没有这个方法
  * File.listFiles/mkdirs 的真实入口是 java.io.UnixFileSystem.list0/createDirectory0，不是 Linux.*

手写 C++ 里的 JNINativeMethod 表时，签名错了编译器不会报（字符串而已），
只有到运行时 RegisterNatives 才失败，而且信息量极小。

## 权威性

**设备上的 dex 是唯一权威**。理由：
  * AOSP 源码（androidxref / googlesource）是某个分支的源码，不等于设备上的产物
  * 厂商会改 libcore；Android 的模块化（APEX）也让版本更难对齐
  * 运行时加载的就是 /apex/com.android.art/javalib/*.jar 里的 dex
  * dexdump 读的是 Dalvik 字节码的方法表 —— 与 ART 解析的结果同源

## 用法

    python tools/device/jni-sig-audit.py \
        --src _ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp

默认审计这些类：libcore/io/Linux、java/io/UnixFileSystem、sun/nio/fs/UnixNativeDispatcher
可用 --class a/b/C 反复指定。

退出码：0 = 全部匹配；1 = 有差异（可用于 CI 卡口）。
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

# 设备上承载 java.* / libcore.* 的 jar（Android 10+ 在 APEX 里）
DEVICE_JARS = [
    "/apex/com.android.art/javalib/core-libart.jar",
    "/apex/com.android.art/javalib/core-oj.jar",
]

DEFAULT_CLASSES = [
    "libcore/io/Linux",
    "java/io/UnixFileSystem",
    "sun/nio/fs/UnixNativeDispatcher",
    "java/lang/Runtime",
]


def find_dexdump():
    """优先用 ANDROID_HOME/build-tools 里的 dexdump。"""
    cands = []
    for root in (
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        os.path.expanduser("~/AppData/Local/Android/Sdk"),
        os.path.expanduser("~/Library/Android/sdk"),
        os.path.expanduser("~/Android/Sdk"),
    ):
        if not root:
            continue
        cands.extend(glob.glob(os.path.join(root, "build-tools", "*", "dexdump*")))
    cands = [c for c in cands if os.path.isfile(c)]
    if not cands:
        sys.exit("找不到 dexdump（需要 Android SDK build-tools）")
    return sorted(cands)[-1]


def pull_jars(adb="adb"):
    """把设备上的 core jar 拉到临时目录，返回 {jar_name: dex_path}。"""
    tmp = tempfile.mkdtemp(prefix="jni-audit-")
    out = {}
    for remote in DEVICE_JARS:
        name = os.path.basename(remote)
        local_jar = os.path.join(tmp, name)
        with open(local_jar, "wb") as f:
            r = subprocess.run([adb, "exec-out", "cat", remote], stdout=f)
        if r.returncode != 0 or os.path.getsize(local_jar) < 1024:
            print(f"  [skip] {remote} 拉取失败")
            continue
        with zipfile.ZipFile(local_jar) as z:
            for n in z.namelist():
                if n.endswith(".dex"):
                    dex_path = os.path.join(tmp, f"{name}.{os.path.basename(n)}")
                    with open(dex_path, "wb") as f:
                        f.write(z.read(n))
                    out[name] = dex_path
                    break
    return tmp, out


def dump_methods(dexdump, dex_path, want_classes):
    """解析 dexdump -d 输出，返回 {(class, name, sig): is_native}。"""
    r = subprocess.run([dexdump, "-d", dex_path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    text = r.stdout
    parts = re.split(r"Class descriptor\s*:\s*'([^']+)'", text)
    result = {}
    for i in range(1, len(parts) - 1, 2):
        desc = parts[i].strip("L;").replace(".", "/")
        if desc not in want_classes:
            continue
        body = parts[i + 1]
        for m in re.finditer(
                r"name\s*:\s*'([^']+)'\s*\n\s*type\s*:\s*'([^']+)'\s*\n\s*access\s*:\s*([^\n]*)",
                body):
            name, sig, acc = m.group(1), m.group(2), m.group(3)
            result[(desc, name, sig)] = ("NATIVE" in acc)
    return result


def parse_src_registrations(src_path):
    """从 C++ 源码里抓 {const_cast<char*>("name"), const_cast<char*>("sig"), ...} 对，
    以及它属于哪个 JNINativeMethod 表（用表名/注释附近的类名推断）。

    简化策略：返回 [(name, sig)] 全部条目，不区分表 —— 由比对阶段按"签名是否在任一表里"判断。
    """
    pat = re.compile(
        r'const_cast<char\*>\("([^"]+)"\)\s*,\s*\n?\s*const_cast<char\*>\("([^"]+)"\)',
        re.M)
    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        return pat.findall(f.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="_ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp")
    ap.add_argument("--class", dest="classes", action="append", default=None)
    ap.add_argument("--adb", default="adb")
    args = ap.parse_args()

    want = [c.replace(".", "/") for c in (args.classes or DEFAULT_CLASSES)]
    dexdump = find_dexdump()
    print(f"dexdump : {dexdump}")
    print(f"审计类  : {', '.join(want)}\n")

    tmp, dexes = pull_jars(args.adb)
    if not dexes:
        sys.exit("没有可用的 dex")

    real = {}
    for name, path in dexes.items():
        got = dump_methods(dexdump, path, want)
        print(f"  {name}: 命中 {len(got)} 个目标方法")
        real.update(got)
    print()

    # 打印设备真实签名
    print("=== 设备上真实的 native 方法 ===")
    for cls in want:
        items = [(k, v) for k, v in real.items() if k[0] == cls]
        if not items:
            print(f"  {cls}: 该类不存在于本次扫描的 jar")
            continue
        print(f"  {cls}:")
        for (c, n, s), nat in sorted(items):
            print(f"    {'NATIVE' if nat else 'java  '}  {n:28} {s}")
    print()

    # 比对源码
    regs = parse_src_registrations(args.src)
    print(f"=== 源码里注册的条目：{len(regs)} 个 ===")
    all_real_sigs = {(n, s) for (c, n, s) in real}
    bad = 0
    for n, s in regs:
        if (n, s) in all_real_sigs:
            print(f"  OK        {n:28} {s}")
        else:
            bad += 1
            print(f"  MISMATCH  {n:28} {s}")
            # 提示同名但签名不同的真实签名
            same = [(c, ss) for (c, nn, ss) in real if nn == n]
            if same:
                for c, ss in same:
                    print(f"              真实在 {c}: {ss}")
            else:
                print(f"              设备上没有任何类有名为 {n} 的方法")
    print()
    if bad:
        print(f"结论：{bad} 条不匹配 —— RegisterNatives 会整批失败（批量注册是全或无）")
    else:
        print("结论：全部匹配")
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
