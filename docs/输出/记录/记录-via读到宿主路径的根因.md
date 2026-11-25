# 诊断：为什么 via 读到了宿主 —— `getOpPackageName` 的必要偏差没有兜底

- 日期：2026-11-25
- 触发：用户怀疑「有必要的 hook 没有实现，导致 via 直接读到宿主」
- 依据：via 解包（`classes.dex` 字符串池）+ AOSP 源码 + 基线源码
- **结论：不是"漏了 hook"，而是「一个必要偏差（`getOpPackageName` 返回宿主）没有兜底」**

---

## 一、先把"包名来源"逐个核对清楚

`sandbox/VirtualContext.kt` 里所有返回包名/路径的方法：

| API | 返回 | 是否 guest |
| --- | --- | --- |
| `getPackageName()` | `guestPackageName` | ✅ |
| `getApplicationInfo().packageName` | `guestPackageName`（L278） | ✅ |
| `getApplicationContext()` | `guestApplication`（L434） | ✅ |
| `createPackageContext(guestPkg, _)` | `this` | ✅ |
| `getExternalFilesDir(type)` | `sandboxManager.getExternalAppFilesDir(...)` | ✅ 沙箱 |
| **`getOpPackageName()`** | **`VirtualConstants.HOST_PACKAGE`** | ❌ **宿主** |

**为什么 `getOpPackageName()` 必须是宿主**（基线源码 L1061 有明确注释）：

```kotlin
// Keep mPackageName as HOST package so ContextImpl's mOpPackageName and
// mAttributionSource use the host package name. This prevents
// AppOpsManager.checkPackage(hostUid, guestPkg) SecurityException when
// guest code accesses Settings/ContentProviders via IPC.
```

→ **这是必要偏差**：guest 跑在容器 uid 下，appops 归因必须用宿主包名，否则
`AppOpsManager.checkPackage(hostUid, guestPkg)` 会抛 SecurityException。

---

## 二、via 确实用到了这条路径

`classes.dex` 字符串池（实测）：

| 字符串 | 次数 |
| --- | --- |
| `getOpPackageName` | **1** |
| `getPackageName` | 1 |
| `getExternalFilesDir` | 2 |
| `getExternalFilesDirs` | 1 |
| `getExternalStorageDirectory` | 1 |
| `getExternalStoragePublicDirectory` | 1 |

**关键**：`getOpPackageName` 在 via 里**有一个调用点**。
而 `Environment.getExternalStorageDirectory()` 返回固定 `/storage/emulated/0`，
**只要用 `getOpPackageName()` 拼 `<...>/Android/data/<pkg>/files/`，得到的就是宿主路径**。

---

## 三、完整因果链（解释"建目录在宿主、写文件在沙箱"）

```
via 启动 (11:07)
  ├─ 某处用 getOpPackageName() 拼 external 路径
  │    → /storage/emulated/0/Android/data/com.nextvm.app/files/{filters,font,gm,logs,offline,proxy,syncing,tabs}
  │    → mkdir 在**宿主**（v142 规则只覆盖 .../data/mark.via/，不覆盖 .../data/com.nextvm.app/）
  │    → 8 个空目录（实测存在、全空）
  │
via 保存 (11:09)
  └─ WebView.saveWebArchive(getExternalFilesDir("offline") + "/<title>.mht")
       → getExternalFilesDir 走 VirtualContext → **沙箱**
       → 3 个 .mht 落到 <instance>/sdcard/Android/data/mark.via/files/offline/ ✓

via 打开"离线"列表
  └─ 列的是**宿主** /storage/emulated/0/Android/data/com.nextvm.app/files/offline/
       → 空 → **看不到**
```

**两边不一致的根源**：**同一个 guest 进程里，"包名"有两个值** ——
`getPackageName()` = `mark.via`，`getOpPackageName()` = `com.nextvm.app`。
用前者的代码进沙箱，用后者的代码逃到宿主。

---

## 四、所以"缺失的 hook"到底是什么

**不是漏了一个方法 hook**，而是**缺一层对"宿主路径"的兜底**：

| 方案 | 做法 | 问题 |
| --- | --- | --- |
| A | v142 增加规则 `/storage/emulated/0/Android/data/com.nextvm.app/` → 沙箱 | ⚠️ **容器自己也要用这个目录**（`virtual/apks`、`files` 等），会自噬 |
| B | hook `Environment.buildExternalStorageAppFilesDirs(pkg)`，把 `com.nextvm.app` 改成 guest | 只覆盖 Java 层这一条，native 直调仍逃逸 |
| C | 区分 `getOpPackageName` 的调用者：appops 相关返回宿主、路径构造返回 guest | 需要调用栈判断，脆弱 |
| **D** | **按 guest 重定向**：`.../data/com.nextvm.app/files/<guest 专属子目录>` → 该 guest 沙箱 | 需要约定目录布局，改动大 |

**架构级限制**：
容器的 `external files` 目录**被自己和所有 guest 共用**（都是 `com.nextvm.app`），
**天然无法区分**"这条路径是容器自己的还是 guest 想要的"。

→ 这解释了为什么原基线在这块"看起来像漏 hook，实际是无解"。

---

## 五、附：这解释了两个此前的疑点

| 疑点 | 解释 |
| --- | --- |
| 上一次（非干净安装）宿主 `tabs/` 里有 2 个文件 | 那次 via 可能走的路径与这次不同（或 `tabs` 用 `getOpPackageName`、`offline` 用 `getPackageName`） |
| `tabs.file_path` 记的是宿主路径 | 同上 —— tab 持久化用了 `getOpPackageName()` 拼的路径 |

**即 via 内部对"用哪个包名"并不一致** —— 有的功能用 `getPackageName()`（进沙箱），
有的用 `getOpPackageName()`（逃宿主）。

---

## 六、结论

> 用户怀疑"有必要的 hook 没实现" —— **方向对，但具体不是缺 hook**。
>
> 真实情况是：**`getOpPackageName()` 返回宿主是必要偏差**（否则 appops 拒绝），
> 而 **via 用它拼 external 路径** → 逃到宿主；
> 容器的 `com.nextvm.app` external 目录**被自己和 guest 共用**，
> 没法简单地"重定向到某个 guest 的沙箱"。
>
> 所以这是**原基线的结构性限制**，不是一行 hook 能补的。
> 按用户要求：**只记录，不修复**。

---

## 七、如果要验证上面这条链（不改容器）

```powershell
# 1) 确认宿主 8 个目录的创建时刻 = via 启动时刻
adb shell "ls -la --time-style=full-iso /storage/emulated/0/Android/data/com.nextvm.app/files/"
#    实测：全部 11:07（= 容器/via 启动），而 .mht 是 11:07/11:09（写沙箱）

# 2) 确认沙箱里 offline 有 3 个 .mht、宿主 offline 为空
adb exec-out run-as com.nextvm.app ls "<instance>/sdcard/Android/data/mark.via/files/offline/"
adb shell "ls /storage/emulated/0/Android/data/com.nextvm.app/files/offline/"

# 3) 若将来能拿到 via 的调用栈，可确认是哪个功能用了 getOpPackageName
```
