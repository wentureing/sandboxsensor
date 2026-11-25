# 对照：为什么 via 在 Gaia 里正常，在我们容器里不正常

- 日期：2026-11-25
- 触发：用户问「为什么 via 在它（Gaia）里面正常」
- 结论先行：**Gaia 的路径镜像同时满足「真实可达」和「看起来正常」两个条件，所以它完全不需要 hook；
  我们两种模式各缺一个条件，所以要么需要 hook（缺可达）、要么暴露沙箱（缺伪装）。**

---

## 一、Gaia 的路径形态（上一轮探针实测，v1.7）

| 探针 | Gaia 内 | 直接安装（对照） |
| --- | --- | --- |
| `J01` getDataDir | `/data/user/0/com.app.hider.master.pro.cn/gaia/data/user/0/com.sandbox.probe` | `/data/user/0/com.sandbox.probe` |
| `J16` nativeLibraryDir | `.../gaia/data/app/com.sandbox.probe/lib/arm64` | `/data/app/~~96L…/lib/arm64` |
| `J18` deviceProtected | `/data/user_de/0/com.app.hider.master.pro.cn/gaia/data/user_de/0/com.sandbox.probe` | `/data/user_de/0/com.sandbox.probe` |
| `P05` maps hostHits | `lines=2868 hostHits=0` | `lines=3034 hostHits=0` |

**关键**：Gaia 返回的路径**本身就是一个真实存在、可 stat 可 opendir 的路径**
（它在容器自己的数据目录里），只是**里面嵌了一段 `/data/user/0/<guest>` 的"长相"**。

**设备未 root**（`ro.debuggable=0`、无 `su`）→ 排除了 bind mount / namespace，
所以它只能是**纯 Java 层的路径返回**，没有任何 VFS 手段。

---

## 二、三种路径返回方式对比（这是本文的核心）

| | 返回给应用的路径 | ① 真实可达？ | ② 看起来正常？ | 需要 hook 吗 |
| --- | --- | --- | --- | --- |
| **Gaia** | `/data/user/0/<容器>/gaia/data/user/0/<guest>/files` | ✅ 是 | ✅ 含原始路径片段 | **❌ 完全不需要** |
| **我们的 pathFidelity** | `/data/user/0/<guest>/files` | ❌ **不存在** | ✅ 是 | ✅ 需要（每个 API 都要翻） |
| **我们的默认模式** | `<sandbox>/files` | ✅ 是 | ❌ 暴露沙箱 | ❌ 不需要（但应用能看到沙箱） |

**这就是答案**：

> **Gaia 的镜像路径同时满足两个条件 —— 它是一个真实路径（syscall 直接成功），
> 又包含 `/data/user/0/<guest>` 这种"看起来正常"的片段。
> 所以应用和文件系统都不需要被说服，什么都不用 hook。**
>
> **我们的两种模式各缺一个**：
> - **pathFidelity**（`syntheticDataDir` = `/data/user/0/<pkg>`）：像正常路径，但**那个目录不存在**
>   → 必须靠 hook 在每个 API 边界翻译 → **漏一个就出现"文件在但值为 0"**
> - **默认模式**（`<sandbox>/files`）：真实可达，但**应用能看到沙箱前缀**

---

## 三、这解释了我们这一路遇到的每一个症状

| 症状 | 原因 |
| --- | --- |
| `File.listFiles()` 拿到 2 条却看不见 | `list0` 已 hook ✓，但 `File.length()` 走 `Os.stat` → **`Linux.stat` 未 hook** → 返回 0 → 应用过滤掉 |
| `external_data` / `obb` / `host_ext` 三个错目标 | 因为**必须靠规则**把宿主路径映射到沙箱，而规则的目标写错了没人发现（Gaia 根本不需要规则） |
| `translated=0` 时看不见任何东西 | 说明那次 via 全用沙箱路径（默认模式），而**列表数据来自另一个路径**（自己拼宿主路径）→ 两侧不一致 |
| 三层 hook 都要覆盖（`UnixFileSystem.*0` / `Linux.*` / libc） | 因为我们是在**API 边界**做替换，每个边界都是独立的翻译点；Gaia 在**路径来源**处就解决了 |

**一句话**：
> **Gaia 把问题解决在"路径从哪来"，我们把它推到了"每次 syscall 用什么路径"。**
> 前者一次性、后者逐点。

---

## 四、为什么我们没有走 Gaia 那条路（诚实说明）

代码里其实**已经有 pathFidelity**（`VirtualConstants.isPathFidelityGuest`），但：

```kotlin
const val CM_PACKAGE = "com.greenpoint.android.mc10086.activity"
fun isPathFidelityGuest(packageName: String): Boolean = packageName == CM_PACKAGE
fun syntheticDataDir(packageName: String): String = "/data/user/0/$packageName"   // ← 缺 Gaia 的中间层
```

**两个差异**：
1. **只对一个特定包生效**（CM-gated 实验），via 不在其中
2. **返回 `/data/user/0/<pkg>`（不存在）**，而 Gaia 返回
   `/data/user/0/<容器>/gaia/data/user/0/<pkg>`（**真实存在**）

**第 2 条是根本性的**：我们的 `syntheticDataDir` 少了一层"容器目录 + gaia 前缀"，
所以它不是一个可达路径，必须依赖 hook 翻译。

---

## 五、如果要走 Gaia 那条路，需要改什么（**只是分析，未执行**）

```
syntheticDataDir(pkg)
  现在： /data/user/0/<pkg>
  改成： /data/user/0/<容器>/gaia/data/user/0/<pkg>
```

**但光改字符串不够** —— 还要：
1. 真的在那个位置创建目录（否则 `exists()` 仍为假）
2. `getExternalFilesDir` 也要返回镜像路径（`/storage/emulated/0/Android/data/<pkg>` →
   `<容器>/gaia/sdcard/Android/data/<pkg>` 之类）
3. 已有的 hook 规则全部改成"镜像路径 → 沙箱"，或者干脆保留现状

**风险**：这是**又一条全链路改造**，且会与现有三层 hook 叠加。
**按规则 10，必须先证明逻辑等价，不能边改边试。**

---

## 六、当前更实际的做法

**继续走现有的"API 边界替换"路线，但把漏掉的边界补齐** —— 因为这条路线已经
修到只剩最后一环：

| 层 | 状态 |
| --- | --- |
| `UnixFileSystem.list0` / `createDirectory0` / `createFileExclusively0` / `getBooleanAttributes0` / `getLastModifiedTime0` / `setLastModifiedTime0` / `setReadOnly0` | ✅ |
| `Linux.open` / `access` / `mkdir` / `unlink` / `rename` / **`stat` / `lstat`** | ✅（stat/lstat 于 `f562fcf` 补） |
| libc GOT patch | ✅ |
| NIO2 `UnixNativeDispatcher` | ❌（实测 app 0–4 次 invoke，可暂缓） |
| `Linux.fstat` | ❌ `FileInputStream` 构造时会用 |

**`f562fcf` 的 `Linux.stat` 就是"文件看得见但列表为空"的最后一环** ——
因为 `File.length()` 是应用过滤条目最常用的判据。

---

## 七、下一步：与 Gaia 的正面对照测试

**已准备**：`dist/probe-for-gaia.apk`（v1.13 探针，537KB，从设备取出）

**要做的**：把它导入 Gaia，在 Gaia 里跑一次，重点看这几个探针项与"我们容器里"的差异：

| 探针项 | 想看什么 |
| --- | --- |
| `J01` getDataDir | Gaia 返回的镜像路径全貌 |
| **`J*` getExternalFilesDir** | **Gaia 里这个返回什么** ← 最关键，我们这里是沙箱路径 |
| `J*` getExternalCacheDir / getObbDir | 同上 |
| `F*` 文件读写（如果探针有） | 在 Gaia 里 stat/length 的值 |

**如果 Gaia 的 `getExternalFilesDir` 也返回镜像路径** → 那就确认了"解决在路径来源处"，
我们可以据此决定是否改 `syntheticDataDir`。

**目前卡点**：Gaia 的 APK 不在设备上，也不在本地工作区 —— 需要用户提供。
