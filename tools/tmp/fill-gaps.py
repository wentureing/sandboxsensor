import io


def read_norm(p):
    with io.open(p, "r", encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n")


def write_lf(p, s):
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(s)


BASE = "_ref/ShitJumpingVM/"

# ---------- 1) VirtualConstants: syntheticMediaDir ----------
P = BASE + "core/model/src/main/java/com/nextvm/core/model/ProcessSlot.kt"
s = read_norm(P)
if "syntheticMediaDir" not in s:
    anchor = '''    /** 合成 OBB 目录 */'''
    assert anchor in s, "syntheticObbDir 注释未找到"
    s = s.replace(anchor,
                  '''    /** 合成 media 目录（getExternalMediaDirs） */
    fun syntheticMediaDir(packageName: String): String =
        "/storage/emulated/0/Android/media/$packageName"

''' + anchor, 1)
    write_lf(P, s)
    print("VirtualConstants: 已加 syntheticMediaDir")
else:
    print("VirtualConstants: 已应用")

# ---------- 2) VirtualContext.getExternalMediaDirs ----------
P = BASE + "core/sandbox/src/main/java/com/nextvm/core/sandbox/VirtualContext.kt"
s = read_norm(P)
if "syntheticMediaDir" not in s:
    OLD = '''    @Suppress("DEPRECATION")
    override fun getExternalMediaDirs(): Array<File> {
        return arrayOf(sandboxManager.getExternalMediaDir(instanceId, guestPackageName))
    }'''
    NEW = '''    @Suppress("DEPRECATION")
    override fun getExternalMediaDirs(): Array<File> {
        val sandboxDir = sandboxManager.getExternalMediaDir(instanceId, guestPackageName)
        return arrayOf(
            if (pathFidelity) {
                File(VirtualConstants.syntheticMediaDir(guestPackageName))
            } else {
                sandboxDir
            }
        )
    }'''
    assert OLD in s, "getExternalMediaDirs 未精确匹配"
    s = s.replace(OLD, NEW, 1)
    write_lf(P, s)
    print("VirtualContext: getExternalMediaDirs 已加 pathFidelity 分支")
else:
    print("VirtualContext: 已应用")

# ---------- 3) PackageManagerProxy: device/credentialProtectedDataDir ----------
P = BASE + "core/binder/src/main/java/com/nextvm/core/binder/proxy/PackageManagerProxy.kt"
s = read_norm(P)
if "deviceProtectedDataDir" not in s:
    OLD = '''                dataDir = VirtualConstants.syntheticDataDir(app.packageName)
                nativeLibraryDir = VirtualConstants.syntheticLibDir(app.packageName)'''
    NEW = '''                dataDir = VirtualConstants.syntheticDataDir(app.packageName)
                // 加密存储目录同样会暴露沙箱，跟 dataDir 一起合成（真机两者常常相同）
                deviceProtectedDataDir = dataDir
                credentialProtectedDataDir = dataDir
                nativeLibraryDir = VirtualConstants.syntheticLibDir(app.packageName)'''
    assert OLD in s, "dataDir 行未精确匹配"
    s = s.replace(OLD, NEW, 1)
    write_lf(P, s)
    print("PackageManagerProxy: 已补 device/credentialProtectedDataDir")
else:
    print("PackageManagerProxy: 已应用")

# ---------- 4) setupAppRedirections: media 规则 ----------
P = BASE + "core/hook/src/main/java/com/nextvm/core/hook/NativeHookBridge.kt"
s = read_norm(P)
if "Android/media/$guestPackageName" not in s:
    anchor = '''        // 宿主包名 external 兜底。'''
    assert anchor in s, "宿主兜底注释未找到"
    ADD = '''        // media 目录 —— getExternalMediaDir 返回 <appDir>/sdcard/Android/media/<pkg>
        val guestMedia = "$sandboxDataDir/sdcard/Android/media/$guestPackageName/"
        addPathRedirection("/storage/emulated/0/Android/media/$guestPackageName/", guestMedia)
        addPathRedirection("/sdcard/Android/media/$guestPackageName/", guestMedia)
        addPathRedirection(
            "/storage/emulated/0/Android/media/$guestPackageName",
            guestMedia.trimEnd('/')
        )
        addPathRedirection("/sdcard/Android/media/$guestPackageName", guestMedia.trimEnd('/'))

'''
    s = s.replace(anchor, ADD + anchor, 1)
    write_lf(P, s)
    print("NativeHookBridge: 已加 media 规则（4 条，含无尾斜杠）")
else:
    print("NativeHookBridge: 已应用")

print("\n完成")
