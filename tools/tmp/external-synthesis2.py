import io


def read_norm(p):
    with io.open(p, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def write_lf(p, s):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


# ---------- 1) VirtualConstants ----------
P1 = "_ref/ShitJumpingVM/core/model/src/main/java/com/nextvm/core/model/ProcessSlot.kt"
s1 = read_norm(P1)
if "syntheticExternalDir" in s1:
    print("VirtualConstants: 已应用")
else:
    anchor = '''    /** Stock-install native lib dir shape. */
    fun syntheticLibDir(packageName: String): String ='''
    assert anchor in s1, "syntheticLibDir 未找到"
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
    s1 = s1.replace(anchor, ADD + anchor, 1)
    write_lf(P1, s1)
    print("VirtualConstants: 已加 3 个合成 external 路径")

# ---------- 2) VirtualContext ----------
P2 = "_ref/ShitJumpingVM/core/sandbox/src/main/java/com/nextvm/core/sandbox/VirtualContext.kt"
s2 = read_norm(P2)
if "syntheticExternalDir" in s2:
    print("VirtualContext: 已应用")
    raise SystemExit(0)

lines = s2.split("\n")


def find_block(start_pred):
    i = next(k for k, l in enumerate(lines) if start_pred(l.strip()))
    depth = 0
    end = None
    for j in range(i, len(lines)):
        depth += lines[j].count("{") - lines[j].count("}")
        if j > i and depth == 0:
            end = j
            break
    return i, end


# 2a) getExternalFilesDir（块体，已存在花括号）
i, end = find_block(lambda t: t.startswith("override fun getExternalFilesDir(type: String?): File {"))
assert end is not None
NEW = '''    override fun getExternalFilesDir(type: String?): File {
        // 先确保沙箱目录存在（无论如何都要建），再决定对外暴露哪条路径。
        val sandboxDir = sandboxManager.getExternalAppFilesDir(instanceId, guestPackageName, type)
        // pathFidelity：对外给 /storage/emulated/0/Android/data/<pkg>/files[/type]（真机形态），
        // I/O 由 setupAppRedirections 的规则映射回 sandboxDir。
        // 详见 docs/架构/架构-syntheticDataDir.md
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
lines[i:end + 1] = NEW

# 2b) getExternalCacheDir（表达式体：从 `= ` 到下一个空行/新成员）
i = next(k for k, l in enumerate(lines) if l.strip().startswith("override fun getExternalCacheDir(): File ="))
j = i + 1
while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith(("override ", "fun ", "}", "//", "@")):
    j += 1
lines[i:j] = '''    override fun getExternalCacheDir(): File {
        val sandboxDir = sandboxManager.getExternalAppCacheDir(instanceId, guestPackageName)
        return if (pathFidelity) {
            File(VirtualConstants.syntheticExternalCacheDir(guestPackageName))
        } else {
            sandboxDir
        }
    }'''.split("\n")

# 2c) getObbDir
i = next(k for k, l in enumerate(lines) if l.strip().startswith("override fun getObbDir(): File ="))
j = i + 1
while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith(("override ", "fun ", "}", "//", "@")):
    j += 1
lines[i:j] = '''    override fun getObbDir(): File {
        val sandboxDir = sandboxManager.getExternalObbDir(instanceId, guestPackageName)
        return if (pathFidelity) {
            File(VirtualConstants.syntheticObbDir(guestPackageName))
        } else {
            sandboxDir
        }
    }'''.split("\n")

write_lf(P2, "\n".join(lines))
print("VirtualContext: 三个 external 方法已加 pathFidelity 分支")
