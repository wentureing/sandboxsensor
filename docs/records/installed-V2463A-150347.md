# 探针记录：installed @ V2463A

- 源报告：`C:/Users/86177/Desktop/轻简虚拟环境开发/probe-reports/report-installed-20261122-150347.json`
- 包名：`com.sandbox.probe`
- 条目：**57**

## 设备

| 属性 | 值 |
| --- | --- |
| `ro.build.version.release` | 15 |
| `ro.build.version.sdk` | 35 |
| `ro.product.model` | V2463A |
| `ro.product.manufacturer` | vivo |
| `ro.product.cpu.abi` | arm64-v8a |
| `PAGESIZE` | 4096 |
| `ro.build.fingerprint` | vivo/PD2463/PD2463:15/AP3A.240905.015.A2_V000L1/compiler250909213731:user/release-keys |

## 启动时序（相对首个标记）

| # | phase | +ms | pid | process |
| ---: | --- | ---: | ---: | --- |
| 1 | `Application.attachBaseContext` | 0 | 17658 | com.sandbox.probe |
| 2 | `ContentProvider.onCreate` | 0 | 17658 | com.sandbox.probe |
| 3 | `Application.onCreate` | 1 | 17658 | com.sandbox.probe |
| 4 | `Activity.onCreate` | 10 | 17658 | com.sandbox.probe |
| 5 | `Activity.onCreate` | 42810 | 17658 | com.sandbox.probe |
| 6 | `Activity.onCreate` | 86086 | 17658 | com.sandbox.probe |

> **承接时机实证**：✅ Provider 早于 Application.onCreate（ContentProvider.onCreate → Application.onCreate）

## Provider 阶段快照（承接时机）

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `P01.provider.getDataDir` | getDataDir | ok | `/data/user/0/com.sandbox.probe` |
| `P02.provider.getFilesDir` | getFilesDir | ok | `/data/user/0/com.sandbox.probe/files` |
| `P03.provider.filesDirExists` | filesDir.exists | ok | `true` |
| `P04.provider.packageName` | getPackageName | ok | `com.sandbox.probe` |
| `P05.provider.myUid` | Process.myUid | ok | `10292` |
| `P06.provider.sourceDir` | applicationInfo.sourceDir | ok | `/data/app/~~kHMqIW_pdMckv6P54r9rSw==/com.sandbox.probe-5BdW558PysvrbEKRix-p1g==/base.apk` |

## Context 目录

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `J01` | getDataDir | ok | `/data/user/0/com.sandbox.probe` |
| `J02` | getFilesDir | ok | `/data/user/0/com.sandbox.probe/files exists=true canWrite=true` |
| `J03` | getCacheDir | ok | `/data/user/0/com.sandbox.probe/cache` |
| `J04` | getCodeCacheDir | ok | `/data/user/0/com.sandbox.probe/code_cache` |
| `J05` | getNoBackupFilesDir | ok | `/data/user/0/com.sandbox.probe/no_backup` |
| `J06` | getDir(probe) | ok | `/data/user/0/com.sandbox.probe/app_probe` |
| `J07` | getDatabasePath+sqlite | ok | `/data/user/0/com.sandbox.probe/databases/probe.db rows=3` |

## 外部存储 / Environment

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `J08` | getExternalFilesDir | ok | `/storage/emulated/0/Android/data/com.sandbox.probe/files` |
| `J09` | getExternalFilesDirs.count | ok | `1` |
| `J10` | getExternalCacheDir | ok | `/storage/emulated/0/Android/data/com.sandbox.probe/cache` |
| `J11` | getExternalMediaDirs[0] | ok | `/storage/emulated/0/Android/media/com.sandbox.probe` |
| `J12` | getObbDir | ok | `/storage/emulated/0/Android/obb/com.sandbox.probe` |
| `J13` | Environment.getExternalStorageDirectory | ok | `/storage/emulated/0` |

## ApplicationInfo

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `J14` | dataDir | ok | `/data/user/0/com.sandbox.probe` |
| `J15` | sourceDir | ok | `/data/app/~~kHMqIW_pdMckv6P54r9rSw==/com.sandbox.probe-5BdW558PysvrbEKRix-p1g==/base.apk exists=true canRead=true` |
| `J16` | nativeLibraryDir | ok | `/data/app/~~kHMqIW_pdMckv6P54r9rSw==/com.sandbox.probe-5BdW558PysvrbEKRix-p1g==/lib/arm64 exists=true` |
| `J17` | splitSourceDirs | ok | `null` |
| `J18` | deviceProtectedDataDir | ok | `/data/user_de/0/com.sandbox.probe` |

## PackageManager

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `J19` | getApplicationInfo.dataDir | ok | `/data/user/0/com.sandbox.probe uid=10292` |
| `J20` | getPackageInfo | ok | `versionName=1.0 versionCode=1 targetSdk=35` |
| `J21` | getInstalledPackages.count | ok | `356` |

## 身份（UID / PID / Build / SELinux）

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `J22` | myUid vs appInfo.uid | ok | `myUid=10292 officialUid=10292 equal=true` |
| `J23` | pid/tid/process | ok | `pid=17658 tid=29529 process=com.sandbox.probe` |
| `J24` | Build | ok | `MANUFACTURER=vivo MODEL=V2463A SDK=35 RELEASE=15 FINGERPRINT=vivo/PD2463/PD2463:15/AP3A.240905.015.A2_V000L1/compiler250909213731:user/rele…` |
| `J25` | SELinux context | ok | `u:r:untrusted_app:s0:c36,c257,c512,c768` |

## 字面量路径（L1 关键用例）

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `J26` | /data/data/<pkg>/files | ok | `mkdirs=true exists=true read=literal path=/data/data/com.sandbox.probe/files/literal.txt` |
| `J27` | /data/user/0/<pkg>/files | ok | `mkdirs=true exists=true read=literal path=/data/user/0/com.sandbox.probe/files/literal.txt` |
| `J28` | exists(/data/data/<pkg>) | ok | `true` |
| `J29` | listFiles(/data/data/<pkg>) | ok | `7:cache,code_cache,files,no_backup,app_probe,databases,shared_prefs` |

## canonicalPath

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `J30` | filesDir canonical vs absolute | ok | `absolute=/data/user/0/com.sandbox.probe/files canonical=/data/data/com.sandbox.probe/files` |
| `J31` | not-exist literal canonical | ok | `/data/data/com.sandbox.probe/files/not_exist_probe` |

## java.io / SharedPreferences

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `J32` | filesDir.listFiles.count | ok | `6` |
| `J33` | SharedPreferences file | ok | `/data/user/0/com.sandbox.probe/shared_prefs/probe_prefs.xml exists=true` |
| `J34` | dataDir.list.count | ok | `7:app_probe,cache,code_cache,databases,files,no_backup,shared_prefs` |

## 设备

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `J35` | pageSize | ok | `SUPPORTED_ABIS=arm64-v8a` |
| `J36` | supported 64bit abis | ok | `arm64-v8a` |

## libc / native

| id | 名称 | 状态 | 值 |
| --- | --- | --- | --- |
| `N01.identity` | N01.identity | ok | `uid=10292 euid=10292 gid=10292 pid=17658 ppid=1583` |
| `N02.proc_cmdline` | N02.proc_cmdline | ok | `com.sandbox.probe\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|\|…` |
| `N03.open_literal` | N03.open_literal | ok | `fd=5 path=/data/data/com.sandbox.probe/files/native_probe.txt` |
| `N04.stat` | N04.stat | ok | `size=5 mode=100600 uid=10292 gid=10292 dev=65081 ino=105146` |
| `N05.access_files_dir` | N05.access_files_dir | ok | `F_OK ok` |
| `N06.fopen_write` | N06.fopen_write | ok | `wrote 5 bytes` |
| `N07.openat_relative` | N07.openat_relative | ok | `dfd=113 fd=124` |
| `N08.readlink_fd` | N08.readlink_fd | ok | `/data/data/com.sandbox.probe/files/native_probe.txt` |
| `N09.syscall_openat` | N09.syscall_openat | ok | `fd=113` |
| `N10.maps_scan` | N10.maps_scan | ok | `lines=3059 base_apk=7 pkg_hit=7 probe_lib=0 first_base_apk=746b004000-746b02c000 r--p 0000c000 fe:39 253536                         /data/a…` |
| `N11.write_ops` | N11.write_ops | ok | `mkdir=1 rename=1 unlink=1 rmdir=1` |
| `N12.syscall_getuid` | N12.syscall_getuid | ok | `uid=10292` |
| `N13.open_user0_prefix` | N13.open_user0_prefix | ok | `fd=113 path=/data/user/0/com.sandbox.probe/files/native_user0.txt` |
| `N14.readlink_user0_fd` | N14.readlink_user0_fd | ok | `/data/data/com.sandbox.probe/files/native_user0.txt` |
| `N15.stat_user0_dir` | N15.stat_user0_dir | ok | `mode=40771 uid=10292 dev=65081` |

## 结论

- 条目总数 **57**，失败 **0**
- 全部通过（作为 golden 基线使用）
