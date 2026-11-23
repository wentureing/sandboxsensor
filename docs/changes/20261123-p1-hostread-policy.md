# P1-2：HostReadPolicy + Robolectric 单测

- 日期：2026-11-23
- 依据：`docs/计划-提升路线图.md` 的 P1-2
- 当前基线：容器 `a7650f2`（P0 已完成，p20 构建成功）

---

## 目标

把「宿主代读到底允不允许某条路径」变成**可自动断言的纯函数**，并用 Robolectric 在 CI 里跑。

---

## 影响范围

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `core/sandbox/src/main/java/com/nextvm/core/sandbox/HostReadPolicy.kt` | **新增** | 纯函数，约 130 行（含注释） |
| `core/sandbox/src/test/java/com/nextvm/core/sandbox/HostReadPolicyTest.kt` | **新增** | 15 条用例，约 200 行 |
| `core/sandbox/build.gradle.kts` | 修改 | 加 `testImplementation`（JUnit + Robolectric）+ `testOptions` |
| `gradle/libs.versions.toml` | 修改 | 加 `robolectric = "4.14"` / `junit = "4.13.2"` |
| `.github/workflows/container-build.yml` | 修改 | 加一次 `testDebugUnitTest` |

**合计**：2 新增 + 3 修改，约 350 行。**不触碰** hook / 路径重定向 / 权限判断以外的逻辑。

---

## 依赖版本（已按官方兼容表核对）

| 项 | 版本 | 依据 |
| --- | --- | --- |
| **Robolectric** | **4.14** | [官方兼容表](https://robolectric.org/compatibility_table/)：`4.14 → API 21–35 / Gradle 8.10.2 / AGP 8.7.1`。我们 AGP **8.7.3** + Gradle **8.9** + compileSdk **35**，**4.14 是唯一匹配**（4.15+ 需 AGP 8.10+，4.16+ 需 8.12+） |
| JUnit | 4.13.2 | Robolectric 4.x 标准搭配 |
| Java | 17 | Android 15 (API 35) 对 Robolectric 的最低要求；项目已是 `jvmTarget = "17"` |

---

## 设计

### 核心接口

```kotlin
object HostReadPolicy {

    /** 允许通过宿主代读访问的前缀（容器自己的外部目录 + 导入目录）。 */
    private val HOST_ALLOWED: List<File> = listOf(
        File("/storage/emulated/0/Android/data/com.nextvm.app"),
        File("/storage/emulated/0/Download"),
    )

    /**
     * 判断 [requested] 是否允许代读。
     *
     * @return 规范化后的真实路径；不允许则 null
     */
    fun resolve(sandboxDataRoot: File, instanceId: String, requested: String): String?
}
```

### 四条硬约束（缺一条就是安全洞）

```kotlin
fun resolve(sandboxDataRoot: File, instanceId: String, requested: String): String? {
    if (requested.isBlank() || requested.length > 4096) return null
    if (requested.contains('\u0000')) return null

    // ① canonicalPath 规范化：解析 ../ 与软链
    val real = runCatching { File(requested).canonicalPath }.getOrNull() ?: return null

    // ② 自己沙箱：前缀后必须加分隔符，防 /x/data/abc 命中 /x/data/abcd
    if (isSafeInstanceId(instanceId)) {
        val own = runCatching {
            File(sandboxDataRoot, instanceId).canonicalPath + File.separator
        }.getOrNull()
        if (own != null && real.startsWith(own)) return real
    }

    // ③ 容器外部区域
    for (allowed in HOST_ALLOWED) {
        val p = runCatching { allowed.canonicalPath + File.separator }.getOrNull() ?: continue
        if (real.startsWith(p)) return real
    }

    // ④ 其他一律拒绝
    return null
}
```

### 白名单

| 类别 | 路径 | 判定 |
| --- | --- | --- |
| guest 自己的沙箱 | `<sandboxDataRoot>/<instanceId>/**` | ✅ |
| 容器外部私有目录 | `/storage/emulated/0/Android/data/com.nextvm.app/**` | ✅ |
| 导入目录 | `/storage/emulated/0/Download/**` | ✅（与 p18 豁免同一份语义） |
| 其他 guest 的沙箱 | `<sandboxDataRoot>/<另一个 instanceId>/**` | ❌ |
| 其他应用 | `/data/data/<其他>`、`/data/user/0/<其他>` | ❌ |
| 系统 | `/system/**`、`/vendor/**`、`/apex/**` | ❌ |
| 公共目录（除 Download） | `/storage/emulated/0/DCIM/**` 等 | ❌ |

---

## 测试用例（15 条）

| # | 输入 | 期望 | 覆盖点 |
| --- | --- | --- | --- |
| 1 | `<root>/<id>/files/a.html` | ✅ | 正常路径 |
| 2 | `<root>/<id>/../<id2>/files/a.html` | ❌ | `..` 逃逸 |
| 3 | `<root>/<id>abc/files/a.html` | ❌ | 前缀攻击 |
| 4 | `<root>/<id2>/files/a.html` | ❌ | 跨实例 |
| 5 | `/data/data/com.other.app/files/x` | ❌ | 其他应用 |
| 6 | `/system/etc/hosts` | ❌ | 系统目录 |
| 7 | `/storage/emulated/0/Android/data/com.nextvm.app/files/x` | ✅ | 容器外部 |
| 8 | `/storage/emulated/0/Download/x.html` | ✅ | 导入目录 |
| 9 | `/storage/emulated/0/DCIM/x.jpg` | ❌ | 非白名单公共目录 |
| 10 | `""` / `"   "` | ❌ | 空 |
| 11 | `<root>/<id>/files/../../../etc/passwd` | ❌ | 深逃逸 |
| 12 | 软链指向外部 | ❌ | canonicalPath 解析后落到白名单外 |
| 13 | `<root>/<id>/files/`（尾斜杠） | ✅ | 尾斜杠 |
| 14 | 含 `\u0000` | ❌ | NUL 注入 |
| 15 | 长度 > 4096 | ❌ | 超长 |

**用例 12 的实现**：在 `@Before` 里用临时目录建一个软链（`Files.createSymbolicLink`），
指向白名单外的路径；如果文件系统不支持软链（Windows 非管理员），该用例 `Assume.assumeTrue` 跳过。

---

## CI 集成

```yaml
# container-build.yml 里，紧跟在 build 之后
- name: Unit tests (Robolectric)
  run: ./gradlew :core:sandbox:testDebugUnitTest --no-daemon --stacktrace
```

**注意**：
- 这是**纯 JVM** 测试，不需要模拟器
- Robolectric 首次会下载 `android-all-instrumented` jar（API 35 约 100 MB），
  落在 `~/.m2` 或 Gradle cache；现有 `gradle-home-8.9-v2` 缓存能部分覆盖
- **失败时不打印全量日志**：`--stacktrace` 只在失败时输出，且我们只读前后十几行

---

## 与「测试执行时机」的关系

| 层次 | 谁执行 | 何时 |
| --- | --- | --- |
| **纯逻辑（本任务）** | Robolectric（JVM） | **CI 构建时自动** |
| **真实设备验证** | `CmdReceiver` 的 `SELFCHECK` 命令 | **手动广播触发**（P1-1） |

两者不重复：Robolectric 验证**算法**，`SELFCHECK` 验证**真机上算出的路径对不对**
（比如 `sandboxDataRoot` 到底是 `files/virtual` 还是别的）。

---

## 风险

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| Robolectric 4.14 与 AGP 8.7.3 的实际兼容问题 | 中 | 先只加依赖跑一个空测试；失败就查官方文档 + 读报错前后十几行 |
| `android-all` jar 下载慢/失败 | 中 | CI 缓存；失败时降级为只跑纯 JUnit（不依赖 Robolectric 的用例） |
| 软链用例在 CI 上不可用 | 低 | `Assume` 跳过 |
| 新增测试依赖影响主构建 | 低 | `testImplementation` 不进 APK |

---

## 回退方案

```powershell
git -C _ref/ShitJumpingVM revert <sha>
# 或只删新增文件 + 还原 build.gradle.kts / libs.versions.toml
```

**本任务不改任何运行时逻辑**，回退零风险。

---

## 验证方法

```powershell
# 本地（若无 Gradle 环境则跳过，靠 CI）
./gradlew :core:sandbox:testDebugUnitTest

# CI
# 看 container-build 的 "Unit tests (Robolectric)" 步骤
```

**判据**：15 条用例全绿；若有用例失败，读报错**前后十几行**并聚合相似文本，不要全文粘贴。

---

## 实际变更

（编码后补）
