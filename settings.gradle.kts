pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "sandbox-probe"

// 探针工程：独立于容器骨架（ShitJumpingVM fork），不与它的 settings / version catalog 共享。
// 构建入口只有显式任务，例如：
//   gradle :probe:assembleDebug
include(":probe")
