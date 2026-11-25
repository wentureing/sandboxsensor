import io

P = "_ref/ShitJumpingVM/core/binder/src/main/java/com/nextvm/core/binder/proxy/PackageManagerProxy.kt"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
nl = "\r\n" if "\r\n" in raw else "\n"

OLD = """                // 加密存储目录同样会暴露沙箱，跟 dataDir 一起合成（真机两者常常相同）
                deviceProtectedDataDir = dataDir
                credentialProtectedDataDir = dataDir"""

NEW = """                // 加密存储目录同样会暴露沙箱，跟 dataDir 一起合成（真机两者常常相同）
                deviceProtectedDataDir = dataDir
                // credentialProtectedDataDir 在 android-35 SDK 里不是 public，只能反射设置；
                // 设不上就算了（该字段用得很少，且失败不影响主路径）。
                runCatching {
                    ApplicationInfo::class.java
                        .getDeclaredField("credentialProtectedDataDir")
                        .apply { isAccessible = true }
                        .set(this@makeApplicationInfoShim, dataDir)
                }"""

if OLD not in raw:
    print("未匹配，退出")
    raise SystemExit(1)

raw = raw.replace(OLD, NEW, 1)
with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(raw)
print("已改为反射设置 credentialProtectedDataDir")
