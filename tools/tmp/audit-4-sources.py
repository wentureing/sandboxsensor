"""把「路径知识」的 4 处独立来源各提取一遍，逐路径族对比，找出已经存在的不一致。

4 处独立来源（第 5 处 g_path_redirects 是 setupAppRedirections 的同步副本，不算独立）：
  A. VirtualConstants.synthetic*            假路径怎么拼
  B. VirtualContext 的 pathFidelity 分支    哪个 API 用哪个合成函数 + 沙箱目标
  C. PackageManagerProxy 的 pathFidelity     ApplicationInfo 哪些字段用哪个
  D. NativeHookBridge.setupAppRedirections  宿主前缀 -> 沙箱目标（真正的规则表）

输出：每个路径族的 (A 合成, B 消费, C 消费, D 规则) 四列对照，标注不一致处。
"""
import io
import re

BASE = "_ref/ShitJumpingVM/"


def read(p):
    with io.open(BASE + p, "r", encoding="utf-8", errors="replace", newline="") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n")


# ---------- A: VirtualConstants 的 synthetic* ----------
vc = read("core/model/src/main/java/com/nextvm/core/model/ProcessSlot.kt")
A = {}
for m in re.finditer(r"fun (synthetic\w+)\(packageName: String\): String =\s*\n\s*\"([^\"]+)\"", vc):
    A[m.group(1)] = m.group(2)

print("=" * 78)
print("A. VirtualConstants 的合成路径（%d 个）" % len(A))
for k, v in A.items():
    print("  %-26s %s" % (k, v))

# ---------- B: VirtualContext 的 pathFidelity 分支用哪个 synthetic ----------
ctx = read("core/sandbox/src/main/java/com/nextvm/core/sandbox/VirtualContext.kt")
B = {}
# 找每个 override fun getXxx 里出现的 VirtualConstants.syntheticYyy
for m in re.finditer(r"override fun (get\w+)\([^)]*\)[^{]*\{(.*?)\n    \}", ctx, re.S):
    fname, body = m.group(1), m.group(2)
    syn = re.findall(r"VirtualConstants\.(synthetic\w+)", body)
    has_pf = "pathFidelity" in body
    B[fname] = (syn, has_pf)

print()
print("=" * 78)
print("B. VirtualContext 的 override（%d 个方法，其中带 pathFidelity 的标 *）" % len(B))
for k, (syn, pf) in B.items():
    mark = "*" if pf else " "
    print("  %s %-26s %s" % (mark, k, ", ".join(syn) if syn else "（直接用 sandboxManager）"))

# ---------- C: PackageManagerProxy 用哪个 synthetic ----------
pmp = read("core/binder/src/main/java/com/nextvm/core/binder/proxy/PackageManagerProxy.kt")
C = {}
block = re.search(r"if \(VirtualConstants\.isPathFidelityGuest\(app\.packageName\)\) \{(.*?)\n            \}", pmp, re.S)
if block:
    body = block.group(1)
    for m in re.finditer(r"(\w+)\s*=\s*(VirtualConstants\.synthetic\w+|\w+)", body):
        C[m.group(1)] = m.group(2)

print()
print("=" * 78)
print("C. PackageManagerProxy 里 pathFidelity 分支设置的字段（%d 个）" % len(C))
for k, v in C.items():
    print("  %-26s = %s" % (k, v))

# ---------- D: setupAppRedirections 的规则 ----------
nhb = read("core/hook/src/main/java/com/nextvm/core/hook/NativeHookBridge.kt")
fn = re.search(r"fun setupAppRedirections\(.*?\n    \}", nhb, re.S)
D = []
if fn:
    for m in re.finditer(r'addPathRedirection\(\s*"([^"]+)"\s*,\s*(\$?\w+|\$?[\w/]*"?[^)]*?)"?\s*\)', fn.group(0)):
        D.append((m.group(1), m.group(2).strip()))
    # 也抓变量形式
    for m in re.finditer(r'val (\w+) = "([^"]+)"', fn.group(0)):
        D.append(("(val %s)" % m.group(1), m.group(2)))

print()
print("=" * 78)
print("D. setupAppRedirections 的规则（%d 条）" % len(D))
for src, dst in D:
    print("  %-58s -> %s" % (src, dst))

# ---------- 逐族对照 ----------
print()
print("=" * 78)
print("逐族对照：A 定义了什么 / B 谁在用 / C 谁在用 / D 有没有对应规则")
issues = []

def family_of(syn_name):
    return syn_name.replace("synthetic", "").lower()

for syn in A:
    fam = family_of(syn)
    b_users = [k for k, (s, pf) in B.items() if syn in s]
    c_users = [k for k, v in C.items() if v.endswith(syn)]
    # D 里有没有这个族的规则：用 sandbox 目标的关键片段粗判
    key = {
        "datadir": "files", "apkpath": "apk", "libdir": "lib",
        "externaldir": "Android/data", "externalcachedir": "cache",
        "obbdir": "Android/obb", "mediadir": "Android/media",
    }.get(fam, fam)
    d_hits = [s for s, _ in D if key in s or key in str(_)]
    print()
    print("  【%s】" % syn)
    print("    A 定义      : %s" % A[syn])
    print("    B VirtualContext : %s" % (", ".join(b_users) if b_users else "**无人使用**"))
    print("    C PackageManager : %s" % (", ".join(c_users) if c_users else "**无人使用**"))
    print("    D 规则命中   : %d 条" % len(d_hits))
    if not b_users and not c_users:
        issues.append("%s 定义了但没人用" % syn)
    if not d_hits:
        issues.append("%s 在 setupAppRedirections 里没有对应规则" % syn)

print()
print("=" * 78)
print("发现的问题")
if issues:
    for i in issues:
        print("  ! " + i)
else:
    print("  （A/B/C/D 四处之间没有结构性不一致）")
