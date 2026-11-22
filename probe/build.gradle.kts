plugins {
    id("com.android.application")
}

// ABI 由 -PprobeAbi= 指定；universal 表示不裁剪
val probeAbi: String = (providers.gradleProperty("probeAbi").orNull ?: "arm64-v8a").trim()

android {
    namespace = "com.sandbox.probe"
    compileSdk = 35
    ndkVersion = "27.1.12297006"

    defaultConfig {
        applicationId = "com.sandbox.probe"
        minSdk = 30
        targetSdk = 35
        versionCode = 5
        versionName = "1.4"

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

    buildTypes {
        getByName("debug") {
            isMinifyEnabled = false
            // 探针必须 debuggable，否则 run-as 不可用
            isDebuggable = true
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
