# 签名漂移：`INSTALL_FAILED_UPDATE_INCOMPATIBLE` 的完整根因

- 日期：2026-11-24
- 现象：`adb install` 报
  `INSTALL_FAILED_UPDATE_INCOMPATIBLE: Existing package com.nextvm.app signatures do not match newer version; ignoring!`
- **根因：`Cache debug keystore` 从未成功保存过 → 每次 CI 构建都生成新的 debug.keystore → 每次签名不同**

---

## 一、决定性证据

### 1.1 `Post Cache debug keystore` 的原始日志（CI #35，Step 26）

```
2026-11-24T08:43:37.8541821Z Post job cleanup.
...
[warning]Path Validation Error: Path(s) specified in the action for caching do(es)
          not exist, hence no cache is being saved.
```

**含义**：job 结束时，`actions/cache` 检查 `path` 里的路径 —— **不存在** →
**不保存缓存** → **下次运行也恢复不到任何东西** → **永远没有"固定的 keystore"**

### 1.2 workflow 的配置（`container-build.yml:117-121`）

```yaml
- name: Cache debug keystore
  uses: actions/cache@v4
  with:
    path: ~/.android/debug.keystore
    key: android-debug-keystore-v1

- name: Build container APK
  working-directory: container
  run: ./gradlew :app:assembleDebug ...
```

**注意**：`actions/cache` 的语义是「**job 开始时恢复，job 结束时保存**」。
- **恢复**：第一次运行时没有缓存 → 跳过
- **保存**：Post 步骤检查 `~/.android/debug.keystore` → **不存在** → 不保存

**所以这条缓存从建立以来**（`android-debug-keystore-v1`）**从未生效过**。

### 1.3 环境变量

```yaml
# L36-37
ANDROID_SDK_ROOT: ${{ github.workspace }}/.android-sdk
ANDROID_HOME:     ${{ github.workspace }}/.android-sdk
```

**`ANDROID_SDK_HOME` 和 `ANDROID_USER_HOME` 都没有设置** ——
按 AGP 的规则，debug keystore 应该落在 `$HOME/.android/debug.keystore`。

**但 Post 步骤说它不存在** → **实际位置不是 `~/.android/`**

**可能的实际位置**（需一次 CI 验证）：
| 候选 | 理由 |
| --- | --- |
| `$ANDROID_SDK_ROOT/.android/debug.keystore` | 某些 AGP 版本把 `ANDROID_SDK_ROOT` 当 `ANDROID_SDK_HOME` 用 |
| `$ANDROID_HOME/.android/debug.keystore` | 同上 |
| `/root/.android/debug.keystore` | 如果 job 以 root 运行，`~` 展开不同 |

---

## 二、关键推论

> **"固定 keystore + 递增 versionCode"这两个条件，我们只满足了后者。**
>
> `versionCode = GITHUB_RUN_NUMBER` 一直在递增 ✓
> **但 keystore 每次都是新的** ✗
>
> **所以 `adb install -r` 必然失败**，因为 Android 要求"签名一致 + versionCode 递增"。

**这也解释了为什么以前"有时候能覆盖安装"**：
- **如果两次构建恰好用了同一个 keystore**（比如 Gradle 缓存命中，或者同一次 job 内构建多次）
- **现在 Gradle home 缓存是固定的**（`Cache Gradle home (fixed key, shared with probe)`），
  但 **keystore 不在 Gradle home 里**（它在 `~/.android/`）

---

## 三、三种修复方案

### 方案 A：从 secret 注入固定 keystore（**最可靠，推荐**）

```yaml
- name: Install fixed debug keystore
  run: |
    mkdir -p ~/.android
    echo "${{ secrets.DEBUG_KEYSTORE_BASE64 }}" | base64 -d > ~/.android/debug.keystore
    chmod 600 ~/.android/debug.keystore

- name: Build container APK
  working-directory: container
  run: ./gradlew :app:assembleDebug ...
```

**前提**：需要一份固定的 debug keystore，base64 后存进 GitHub Secret。

**怎么得到这份 keystore**：
```bash
# 一次性生成（本地）
keytool -genkeypair -v -keystore debug.keystore -storepass android \
  -alias androiddebugkey -keypass android -keyalg RSA -keysize 2048 \
  -validity 10000 -dname "CN=Android Debug,O=Android,C=US"
# base64 后贴进 Secret
base64 -w0 debug.keystore
```

**优点**：签名永久固定，不依赖任何缓存机制
**缺点**：需要一次性配置 Secret

### 方案 B：定位实际路径，修 `Cache` 的 path（**次选**）

在 workflow 里加一步诊断：

```yaml
- name: Locate debug keystore
  if: always()
  run: |
    echo "HOME=$HOME"
    echo "ANDROID_SDK_HOME=${ANDROID_SDK_HOME:-<unset>}"
    find / -name "debug.keystore" 2>/dev/null | head -20
```

然后按实际路径改 `path`。

**优点**：不改签名机制
**缺点**：要一次 CI 试错；且**已经漂移的签名无法恢复**（用户仍需卸载一次）

### 方案 C：`Cache` 的 path 用通配（**勉强**）

```yaml
path: |
  ~/.android/debug.keystore
  ${{ github.workspace }}/.android/debug.keystore
  ${{ env.ANDROID_SDK_ROOT }}/.android/debug.keystore
```

**优点**：一次覆盖三个候选
**缺点**：`actions/cache` 对不存在的路径仍会 warning；如果实际位置在第四个地方就还是失败

---

## 四、推荐：**A + B 组合**

1. **先用 B 的诊断步骤确认实际位置**（一次 CI，~4 分钟）
2. **然后改用 A**（从 secret 注入）—— 因为 A 不依赖路径，永久有效

**如果暂时不想配 Secret**：先用 B 定位，然后修 `path`。

---

## 五、当前阻塞与影响

| 项 | 状态 |
| --- | --- |
| 修复 1（双轨沙箱）已提交 | ✅ `1d3e2bb`（容器）+ `698fad0`（主仓库） |
| CI #35 构建成功 | ✅ `conclusion=success`，产物 `container-shitjumpingvm-698fad0.apk` |
| **能否装到现有设备** | ❌ **不能**（签名不匹配） |
| **绕过方式** | `adb uninstall com.nextvm.app`（**会清掉沙箱数据和 appops 授权**） |

**这意味着**：验证修复 1 需要**卸载重装**，代价是：
- 丢失沙箱里的 2 个实例（探针 v1.8 + Via）
- 丢失 `MANAGE_EXTERNAL_STORAGE` 授权

**如果不卸载** → **无法验证修复 1**。

---

## 六、一个附带的重要结论

> **"签名固定"这件事，我们一直以为已经做了（AGENTS.md 里写了 `Cache debug keystore`），
> 但实际从未生效。**
>
> **这是"配置写对了但没验证"的典型 —— 和前面几次"静态搜索/日志级别当结论"是同一类问题。**

**建议**：AGENTS.md 的"构建变体与签名"章节要补一句：
> **验证方法**：连续两次 CI 的 APK 用同一份 `apksigner verify --print-certs` 对比证书指纹，
> **必须完全一致**；不一致就说明 keystore 没固定。
