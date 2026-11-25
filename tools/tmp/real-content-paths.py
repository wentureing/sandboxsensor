import io
import re

P = "_ref/ShitJumpingVM/core/sandbox/src/main/java/com/nextvm/core/sandbox/VirtualContext.kt"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
raw = raw.replace("\r\n", "\n").replace("\r", "\n")

if "CONTENT_PATHS_USE_REAL" in raw:
    print("已应用")
    raise SystemExit(0)

# 1) 在 class 顶部（pathFidelity 定义之后）加一个开关常量
anchor = 'private val pathFidelity: Boolean =\n        VirtualConstants.isPathFidelityGuest(guestPackageName)'
assert anchor in raw, "pathFidelity 定义未找到"

SWITCH = anchor + '''

    /**
     * 内容目录（files/cache/external/obb/media）是否用合成路径。
     *
     * **2026-11-25 起默认 false**：WebView 的渲染跑在 isolated 进程
     * （com.google.android.webview:sandboxed_process0，独立 uid），它既没有我们的
     * hook、也不受我们的路径翻译约束。假路径（/data/user/0/<guest>/...）在真机上
     * 根本不存在，WebView 打开 file:// 会直接 ERR_FILE_NOT_FOUND —— 实测 W18 与
     * 主页空白都是这个原因。
     *
     * 保留 pathFidelity 的部分：getDataDir() / ApplicationInfo.dataDir 仍然返回
     * 合成路径（那是反双开检测最常看的地方），其余内容目录返回**真实沙箱路径**，
     * 这样文件真的存在，WebView 也能读到。
     *
     * 代价：guest 自己看到的内容目录里含宿主包名（暂时接受）。
     * 详见 docs/架构/架构-WebView假路径换真路径.md
     */
    private val contentPathsUseReal: Boolean = false

    private fun contentDir(sandboxDir: File, synthetic: () -> File): File =
        if (pathFidelity && !contentPathsUseReal) synthetic() else sandboxDir
'''

raw = raw.replace(anchor, SWITCH, 1)

# 2) 把各内容目录方法改成「真路径优先」
# getFilesDir
raw = re.sub(
    r"override fun getFilesDir\(\): File \{\n(?:.*\n)*?\s*return dir\n\s*\}",
    '''override fun getFilesDir(): File {
        val sandboxDir = sandboxManager.getFilesDir(instanceId)
        val dir = contentDir(sandboxDir) {
            File(VirtualConstants.syntheticDataDir(guestPackageName), "files")
        }
        com.nextvm.core.common.DebugTrace.detail("ctx", "getFilesDir -> ${dir.absolutePath}")
        return dir
    }''', raw, count=1)

# getCacheDir
raw = re.sub(
    r"override fun getCacheDir\(\): File =\n(?:.*\n)*?\s*(?=\n\s*override|\n\s*fun)",
    '''override fun getCacheDir(): File =
        contentDir(sandboxManager.getCacheDir(instanceId)) {
            File(VirtualConstants.syntheticDataDir(guestPackageName), "cache")
        }

''', raw, count=1)

# getCodeCacheDir
raw = re.sub(
    r"override fun getCodeCacheDir\(\): File =\n(?:.*\n)*?\s*(?=\n\s*override|\n\s*fun)",
    '''override fun getCodeCacheDir(): File =
        contentDir(sandboxManager.getCodeCacheDir(instanceId)) {
            File(VirtualConstants.syntheticDataDir(guestPackageName), "code_cache")
        }

''', raw, count=1)

# getNoBackupFilesDir
raw = re.sub(
    r"override fun getNoBackupFilesDir\(\): File =\n(?:.*\n)*?\s*(?=\n\s*override|\n\s*fun)",
    '''override fun getNoBackupFilesDir(): File =
        contentDir(File(appDir, "no_backup").also { it.mkdirs() }) {
            File(VirtualConstants.syntheticDataDir(guestPackageName), "no_backup")
        }

''', raw, count=1)

# getExternalFilesDir：已有块体，把分支条件换成 contentDir 语义
raw = raw.replace(
    '''        val dir = if (pathFidelity) {
            val base = VirtualConstants.syntheticExternalDir(guestPackageName)
            File(if (type != null) "$base/files/$type" else "$base/files")
        } else {
            sandboxDir
        }''',
    '''        val dir = contentDir(sandboxDir) {
            val base = VirtualConstants.syntheticExternalDir(guestPackageName)
            File(if (type != null) "$base/files/$type" else "$base/files")
        }''', 1)

# getExternalCacheDir
raw = raw.replace(
    '''        return if (pathFidelity) {
            File(VirtualConstants.syntheticExternalCacheDir(guestPackageName))
        } else {
            sandboxDir
        }''',
    '''        return contentDir(sandboxDir) {
            File(VirtualConstants.syntheticExternalCacheDir(guestPackageName))
        }''', 1)

# getObbDir
raw = raw.replace(
    '''        return if (pathFidelity) {
            File(VirtualConstants.syntheticObbDir(guestPackageName))
        } else {
            sandboxDir
        }''',
    '''        return contentDir(sandboxDir) {
            File(VirtualConstants.syntheticObbDir(guestPackageName))
        }''', 1)

# getExternalMediaDirs
raw = raw.replace(
    '''        return arrayOf(
            if (pathFidelity) {
                File(VirtualConstants.syntheticMediaDir(guestPackageName))
            } else {
                sandboxDir
            }
        )''',
    '''        return arrayOf(
            contentDir(sandboxDir) { File(VirtualConstants.syntheticMediaDir(guestPackageName)) }
        )''', 1)

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(raw)
print("已改：内容目录走真路径（contentPathsUseReal=false），getDataDir 保留合成路径")
