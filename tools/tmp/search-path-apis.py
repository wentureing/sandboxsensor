"""从 AOSP 源码横向搜索「所有可能暴露沙箱路径的 API」，与我们已覆盖的对照。

覆盖来源（我们已有的）：
  VirtualConstants.syntheticDataDir / syntheticApkPath / syntheticLibDir
                    / syntheticExternalDir / syntheticExternalCacheDir / syntheticObbDir

待搜索的 AOSP 来源：
  core/java/android/app/ContextImpl.java          所有 get*Dir / get*Path
  core/java/android/content/pm/ApplicationInfo.java  路径字段
  core/java/android/content/pm/PackageInfo.java
  core/java/android/os/Environment.java          （已分析，不需要改）
  core/java/android/os/storage/StorageVolume.java
"""
import io
import re
import subprocess

FWB = "tools/tmp/fwb"


def git_show(path):
    r = subprocess.run(["git", "-C", FWB, "show", "HEAD:" + path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    return r.stdout


FILES = [
    "core/java/android/app/ContextImpl.java",
    "core/java/android/content/pm/ApplicationInfo.java",
    "core/java/android/os/storage/StorageVolume.java",
    "core/java/android/os/Environment.java",
]

# 我们已合成的路径形状（用于判断"是否已被覆盖"）
COVERED_PATTERNS = [
    ("data 目录", r"/data/user/0/<pkg>"),
    ("APK", r"/data/app/~~<token>==/<pkg>-<token>==/base\.apk"),
    ("lib", r"/data/app/~~<token>==/<pkg>-<token>==/lib/arm64"),
    ("external data", r"/storage/emulated/0/Android/data/<pkg>"),
    ("external cache", r"/storage/emulated/0/Android/data/<pkg>/cache"),
    ("obb", r"/storage/emulated/0/Android/obb/<pkg>"),
]

def main():
    print("=" * 78)
    print("AOSP 里所有「返回路径」的入口（横向搜索）")
    print("=" * 78)

    for f in FILES:
        src = git_show(f)
        if src is None:
            print(f"\n[{f}] 读取失败（blob 未下载）")
            continue
        print(f"\n--- {f} ({len(src.splitlines())} 行) ---")

        # 方法签名（返回 File / String / File[] 且名字含 Dir / Path / File）
        methods = []
        for m in re.finditer(
                r"^\s*(?:public|protected)\s+(?:@\w+\s+)*(?:static\s+)?(File|String|File\[\]|String\[\])\s+"
                r"(get\w*(?:Dir|Dirs|Path|Paths|File|Files)\w*)\s*\(",
                src, re.M):
            methods.append((m.group(2), m.group(1)))
        # 字段（ApplicationInfo / StorageVolume 里的路径字段）
        fields = []
        for m in re.finditer(r"^\s*(?:public|protected)\s+(?:@\w+\s+)*(String|File)\s+(\w*(?:Dir|Path|File|Apk|Source)\w*)\s*;",
                             src, re.M):
            fields.append((m.group(2), m.group(1)))

        seen = set()
        for name, typ in methods + fields:
            if name in seen:
                continue
            seen.add(name)
            print(f"  {typ:9} {name}")

    print()
    print("=" * 78)
    print("我们已合成的路径形状（对照用）")
    print("=" * 78)
    for label, pat in COVERED_PATTERNS:
        print(f"  {label:16} {pat}")


if __name__ == "__main__":
    main()
