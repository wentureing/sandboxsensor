import io

# ---------- 1) VirtualConstants 新增三个合成路径 ----------
P1 = "_ref/ShitJumpingVM/core/model/src/main/java/com/nextvm/core/model/ProcessSlot.kt"
with io.open(P1, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
nl = "\r\n" if "\r\n" in raw else "\n"

if "syntheticExternalDir" not in raw:
    anchor = '''    /** Stock-install native lib dir shape. */
    fun syntheticLibDir(packageName: String): String ='''
    assert anchor in raw, "syntheticLibDir 未找到"
    ADD = '''    /** 合成 external data 根：/storage/emulated/0/Android/data/<pkg>（真机形态，实际不存在） */
    fun syntheticExternalDir(packageName: String): String =
        "/storage/emulated/0/Android/data/$packageName"

    /** 合成 external cache 目录（与 files 同级） */
    fun syntheticExternalCacheDir(packageName: String): String =
        "/storage/emulated/0/Android/data/$packageName/cache"

    /** 合成 OBB 目录 */
    fun syntheticObbDir(packageName: String): String =
        "/storage/emulated/0/Android/obb/$packageName"

'''
    raw = raw.replace(anchor, ADD + anchor, 1)
    with io.open(P1, "w", encoding="utf-8", newline="") as f:
        f.write(raw)
    print("VirtualConstants: 已加 3 个合成 external 路径")
else:
    print("VirtualConstants: 已应用")

# ---------- 2) VirtualContext 三个方法加 pathFidelity 分支 ----------
P2 = "_ref/ShitJumpingVM/core/sandbox/src/main/java/com/nextvm/core/sandbox/VirtualContext.kt"
with io.open(P2, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
nl = "\r\n" if "\r\n" in raw else "\n"
lines = raw.split(nl)

if "syntheticExternalDir" in raw:
    print("VirtualContext: 已应用")
    raise SystemExit(0)

# --- 2a) getExternalFilesDir ---
i = next(i for i, l in enumerate(lines) if l.strip().startswith("override fun getExternalFilesDir(type: String?): File {"))
depth = 0
end = None
for j in range(i, len(lines)):
    depth += lines[j].count("{") - lines[j].count("}")
    if j > i and depth == 0:
        end = j
        break
assert end is not None

NEW_FILES = '''    override fun getExternalFilesDir(type: String?): File {
        val sandboxDir = sandboxManager.getExternalAppFilesDir(instanceId, guestPackageName, type)
        // pathFidelity：对外给 /storage/emulated/0/Android/data/<pkg>/files[/type]（真机形态），
        // I/O 由 setupAppRedirections 的规则映射回 sandboxDir。见 docs/架构/架构-syntheticDataDir.md
        val dir = if (pathFidelity) {
            val base = VirtualConstants.syntheticExternalDir(guestPackageName)
            File(if (type != null) "$base/files/$type" else "$base/files")
        } else {
            sandboxDir
        }
        com.nextvm.core.common.DebugTrace.detail(
            "ctx", "getExternalFilesDir(type=$type) -> ${dir.absolutePath} exists=${dir.exists()}"
        )
        return dir
    }'''.split("\n")
lines[i:end + 1] = NEW_FILES

# --- 2b) getExternalCacheDir ---
i = next(i for i, l in enumerate(lines) if l.strip().startswith("override fun getExternalCacheDir(): File ="))
# 表达式体：连续到该行末尾（可能下一行是 sandboxManager 调用）
j = i + 1
while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith(("override", "fun", "}", "//", "@")):
    j += 1
NEW_CACHE = '''    override fun getExternalCacheDir(): File {
        val sandboxDir = sandboxManager.getExternalAppCacheDir(instanceId, guestPackageName)
        return if (pathFidelity) {
            File(VirtualConstants.syntheticExternalCacheDir(guestPackageName))
        } else {
            sandboxDir
        }
    }'''.split("\n")
lines[i:j] = NEW_CACHE

# --- 2c) getObbDir ---
i = next(i for i, l in enumerate(lines) if l.strip().startswith("override fun getObbDir(): File ="))
j = i + 1
while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith(("override", "fun", "}", "//", "@")):
    j += 1
NEW_OBB = '''    override fun getObbDir(): File {
        val sandboxDir = sandboxManager.getExternalObbDir(instanceId, guestPackageName)
        return if (pathFidelity) {
            File(VirtualConstants.syntheticObbDir(guestPackageName))
        } else {
            sandboxDir
        }
    }'''.split("\n")
lines[i:j] = NEW_OBB

with io.open(P2, "w", encoding="utf-8", newline="") as f:
    f.write(nl.join(lines))
print("VirtualContext: 三个 external 方法已加 pathFidelity 分支")
