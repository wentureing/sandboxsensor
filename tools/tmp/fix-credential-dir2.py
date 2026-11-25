import io

P = "_ref/ShitJumpingVM/core/binder/src/main/java/com/nextvm/core/binder/proxy/PackageManagerProxy.kt"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    raw = f.read()
nl = "\r\n" if "\r\n" in raw else "\n"

OLD = '''                // 加密存储目录同样会暴露沙箱，跟 dataDir 一起合成（真机两者常常相同）
                deviceProtectedDataDir = dataDir
                credentialProtectedDataDir = dataDir'''

NEW = '''                // 加密存储目录同样会暴露沙箱，跟 dataDir 一起合成（真机两者常常相同）
                deviceProtectedDataDir = dataDir
                // credentialProtectedDataDir 在 android-35 SDK 里不是 public（编译报
                // Unresolved reference），只能反射设置；设不上就跳过 —— 该字段用得很少。
                runCatching {
                    ApplicationInfo::class.java
                        .getDeclaredField("credentialProtectedDataDir")
                        .apply { isAccessible = true }
                        .set(this, dataDir)
                }'''

assert OLD in raw, "未匹配"
raw = raw.replace(OLD, NEW, 1)

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(raw)
print("已改为反射设置 credentialProtectedDataDir（用 this，不是 this@xxx）")
