plugins {
    id("com.android.application")
}

// ABI 由 -PprobeAbi= 指定；universal 表示不裁剪
val probeAbi: String = (providers.gradleProperty("probeAbi").orNull ?: "arm64-v8a").trim()

// ---- 签名凭据（与容器同一套 keystore，保证 adb install -r 可覆盖）----
// 参考 C:\Users\86177\Desktop\阅读器开发：显式 signingConfigs + 绝对路径，
// 不依赖 AGP 对 ~/.android/debug.keystore 的隐式推断（user.home 与 $HOME 不一致时会静默新建）。
// 凭据优先级：CI 环境变量 > local.properties > 无（退回 AGP 默认 debug 签名）。
val signingProps = java.util.Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
val ksStore: String? = System.getenv("STORE_FILE_NAME") ?: signingProps.getProperty("STORE_FILE_NAME")
val ksStorePwd: String? = System.getenv("KEYSTORE_PASSWORD") ?: signingProps.getProperty("KEYSTORE_PASSWORD")
val ksAlias: String? = System.getenv("STORE_ALIAS") ?: signingProps.getProperty("STORE_ALIAS")
val ksKeyPwd: String? = System.getenv("KEY_PASSWORD") ?: signingProps.getProperty("KEY_PASSWORD")
val hasFixedKeystore: Boolean =
    ksStore != null && java.io.File(ksStore).exists() && ksStorePwd != null
logger.lifecycle(
    "[signing] probe fixed keystore = " + (if (hasFixedKeystore) "$ksStore (exists)" else "NOT FOUND") +
        " | user.home=" + System.getProperty("user.home")
)

android {
    namespace = "com.sandbox.probe"
    compileSdk = 35
    ndkVersion = "27.1.12297006"

    defaultConfig {
        applicationId = "com.sandbox.probe"
        minSdk = 30
        targetSdk = 35
        versionCode = 10
        versionName = "1.9"

        if (probeAbi != "universal") {
            ndk {
                abiFilters += probeAbi.split(",").map { it.trim() }.filter { it.isNotEmpty() }
            }
        }

        externalNativeBuild {
            cmake {
                arguments += listOf("-DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON")
                cppFlags += "-std=c++17"
            }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    // 显式签名：不依赖 AGP 对 ~/.android/debug.keystore 的隐式推断
    signingConfigs {
        if (hasFixedKeystore) {
            create("fixedDebug") {
                storeFile = file(ksStore!!)
                storePassword = ksStorePwd
                keyAlias = ksAlias ?: "androiddebugkey"
                keyPassword = ksKeyPwd ?: ksStorePwd
            }
        }
    }

    buildTypes {
        getByName("debug") {
            isMinifyEnabled = false
            // 探针必须 debuggable，否则 run-as 不可用
            isDebuggable = true
            if (hasFixedKeystore) {
                signingConfig = signingConfigs.getByName("fixedDebug")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        jniLibs {
            useLegacyPackaging = false
        }
    }
}
