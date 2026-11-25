import io

P = "docs/输出/调研/调研-AOSP路径API对照与pathFidelity遗漏.md"
with io.open(P, "r", encoding="utf-8", newline="") as f:
    s = f.read()
nl = "\r\n" if "\r\n" in s else "\n"

OLD_P0 = """### P0 —— 直接暴露沙箱，且是常用路径

| # | API | 为什么重要 |
| --- | --- | --- |
| **1** | **`Context.getFileStreamPath(name)`** | `openFileOutput` / `openFileInput` / `deleteFile` / `fileList` / `getFileStreamPath` 的**唯一底层**。它返回 `<dataDir>/files/<name>`。若 `getDataDir` 给了假路径而它给真路径，同一份文件会有两个"地址"，读写错位 |
| **2** | **`Context.getPackageCodePath()`** | 返回 APK 路径 → 暴露 `<sandbox>` 或暴露"双开" |
| **3** | **`Context.getPackageResourcePath()`** | 同上（多数 ROM 两者相同） |"""

NEW_P0 = """### 更正：原 P0 三条是**误报**，它们已自动跟随

| # | API | 实际结论 |
| --- | --- | --- |
| 1 | `Context.getFileStreamPath(name)` | **不需要单独处理** —— `Context` 的默认实现是 `makeFilename(getFilesDir(), name)`，而 `getFilesDir()` 已在 pathFidelity 分支返回合成路径，**自动跟随** |
| 2 | `Context.getPackageCodePath()` | **不需要** —— 返回 `ApplicationInfo.sourceDir`，而 `PackageManagerProxy` 已在 pathFidelity 时设成 `syntheticApkPath` |
| 3 | `Context.getPackageResourcePath()` | 同上，走 `sourceDir` / `publicSourceDir` |

**实测依据**（`PackageManagerProxy.kt` L752-758）：

```kotlin
if (VirtualConstants.isPathFidelityGuest(app.packageName)) {
    dataDir = VirtualConstants.syntheticDataDir(app.packageName)
    nativeLibraryDir = VirtualConstants.syntheticLibDir(app.packageName)
    val synthApk = VirtualConstants.syntheticApkPath(app.packageName)
    sourceDir = synthApk
    publicSourceDir = synthApk
}
```

**教训**：这已经是本轮第三次「凭印象判定遗漏 → 实际已覆盖」。
判断某路径有没有被合成，必须**搜代码里有没有设置它的地方**，
不能只看 `VirtualContext` 有没有 override —— `ApplicationInfo` 的字段是在
`PackageManagerProxy` 里设的。"""

assert OLD_P0 in s, "P0 段未匹配"
s = s.replace(OLD_P0, NEW_P0, 1)

# P1 表逐行更新
repl = [
    ("| 4 | `Context.getExternalMediaDirs()` | 已记录；`Android/media/<pkg>` 仍返回沙箱 |",
     "| 4 | ~~`Context.getExternalMediaDirs()`~~ | **已补**：`syntheticMediaDir` + `VirtualContext` 分支 + 4 条 media 规则 |"),
    ("| 5 | `ApplicationInfo.dataDir` | 需先确认与 `Context.getDataDir()` 的关系（v146 教训） |",
     "| 5 | ~~`ApplicationInfo.dataDir`~~ | **已覆盖**（`PackageManagerProxy` L753） |"),
    ("| 6 | `ApplicationInfo.deviceProtectedDataDir` | 设备加密存储目录 |",
     "| 6 | ~~`ApplicationInfo.deviceProtectedDataDir`~~ | **已补**（= `dataDir`） |"),
    ("| 7 | `ApplicationInfo.credentialProtectedDataDir` | 凭据加密存储目录 |",
     "| 7 | ~~`ApplicationInfo.credentialProtectedDataDir`~~ | **已补**（= `dataDir`） |"),
]
for a, b in repl:
    if a in s:
        s = s.replace(a, b, 1)
    else:
        print("  未匹配:", a[:40])

# 在 P1 标题下追加一条「本轮补齐」说明
anchor = "### P1 —— 会暴露，但不那么常用"
if anchor in s:
    s = s.replace(anchor, anchor + "\n\n> **2026-11-25 已补齐 4~7 四项**（见下文第七节的完成记录）。", 1)

# 末尾追加完成记录
s = s.rstrip() + "\n" + """
---

## 八、2026-11-25 补齐记录

| 改动 | 文件 | 内容 |
| --- | --- | --- |
| 1 | `core/model/.../ProcessSlot.kt` | 新增 `syntheticMediaDir(pkg)` = `/storage/emulated/0/Android/media/<pkg>` |
| 2 | `core/sandbox/.../VirtualContext.kt` | `getExternalMediaDirs()` 加 `pathFidelity` 分支 |
| 3 | `core/binder/.../PackageManagerProxy.kt` | `deviceProtectedDataDir` / `credentialProtectedDataDir` 设为 `dataDir`（合成值） |
| 4 | `core/hook/.../NativeHookBridge.kt` | `setupAppRedirections` 新增 4 条 media 规则（`/storage/emulated/0/Android/media/` 与 `/sdcard/Android/media/`，各含/不含尾斜杠） |

**第 4 条是关键**：只合成路径而不加规则，就是用户说的"危害实际读写"。
T39（每个合成路径都有对应规则）会守住这一点。
"""

with io.open(P, "w", encoding="utf-8", newline="") as f:
    f.write(s)
print("文档已更正")
