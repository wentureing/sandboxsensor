# 方案评估：把沙箱根路径迁到外部存储（Gaia 的做法）

- 日期：2026-11-22
- 前提确认：**Gaia 已知一定做到了**（用户确认）
- 存档点：`pre-webview-support` = `a528e09`

---

## 一、Gaia 的路径 vs 我们的路径

| 容器 | 沙箱根 | 挂载类型 |
| --- | --- | --- |
| **Gaia** | `/sdcard/Android/data/com.app.hider.master.pro.cn/gaia/<pkg>/` | **外部存储（FUSE/sdcardfs）** |
| **NEXTVM** | `/data/user/0/com.nextvm.app/app_virtual/` | **内部存储（ext4/f2fs）** |

**这个差异正好对应 WebView 的行为差异**：

| 读取方 | 读内部存储的容器路径 | 读外部存储的容器路径 |
| --- | --- | --- |
| 容器自己（guest 主进程） | ✅ 有权限 | ✅ 有权限 |
| **WebView renderer（isolated uid）** | ❌ **越权，被拒** | ⚠️ **可能允许**（外部存储的权限模型更宽） |

**推测机制**：
- WebView renderer 是 isolated uid，**对 `/data/user/0/` 下别的应用目录没有任何权限**
- 但 `/sdcard` 走 **FUSE + MediaProvider**，权限检查走 appop / media 权限，而不是纯 DAC
- 所以 isolated 进程读 `/sdcard/Android/data/<容器>/` 在部分版本上能通过

**这与你的实测吻合**：
- Android 15：WebView 能访问 `/sdcard/Download` ✓
- Android 12：`denied`（更严格的 SELinux 策略）

---

## 二、改动点：只有一行

`app/src/main/java/com/nextvm/app/AppModule.kt`：

```kotlin
@Provides
@Singleton
fun provideSandboxManager(@ApplicationContext context: Context): SandboxManager {
    val virtualRoot = context.getDir("virtual", Context.MODE_PRIVATE)   // ← 现状
    return SandboxManager(context, virtualRoot)
}
```

`context.getDir("virtual", MODE_PRIVATE)` 返回 `/data/user/0/<pkg>/app_virtual/`。

**改为**：

```kotlin
@Provides
@Singleton
fun provideSandboxManager(@ApplicationContext context: Context): SandboxManager {
    // 沙箱根路径策略（sandboxsensor）：
    //   internal = /data/user/0/<pkg>/app_virtual        （默认，性能好）
    //   external = /sdcard/Android/data/<pkg>/files/virtual  （WebView 可见，慢）
    // 用 external 的原因：WebView 的 renderer 是 isolated uid，
    // 读不到别的应用的内部目录，但外部存储的权限模型更宽。
    val useExternal = context
        .getSharedPreferences("sandbox", Context.MODE_PRIVATE)
        .getBoolean("external_sandbox", false)
    val virtualRoot = if (useExternal) {
        File(context.getExternalFilesDir(null), "virtual").also { it.mkdirs() }
    } else {
        context.getDir("virtual", Context.MODE_PRIVATE)
    }
    return SandboxManager(context, virtualRoot)
}
```

**为什么只改这里就够**：
- `virtualRoot` 是通过 Hilt 构造注入的
- `SandboxManager` 内部所有路径都从它派生（`dataRoot = File(virtualRoot, VIRTUAL_DATA_DIR)`）
- 硬编码的 7 处 `/data/user/0/` 都是**虚拟路径**（面向 guest 的），不是沙箱根，**不需要改**

---

## 三、工作量

| 项 | 工作量 | 说明 |
| --- | --- | --- |
| 改 `provideSandboxManager` | **0.5 小时** | 1 处 + 开关 |
| 加设置项（可选 UI 开关） | 1 小时 | 若要做成用户可切换 |
| **数据迁移逻辑** | **4 小时** | 旧沙箱在内部存储，切换后 guest 需要重装；或写 `moveDirectory` |
| 回归测试 | **1 天** | 装 Via + 探针，测：启动 / WebView 主页 / 长按 / 文件读写 / 性能 |
| **合计** | **1–1.5 天** | 不含迁移的话 0.5 天 |

**注意**：这**不是"改架构"**。它只换了一个路径来源，模块依赖、类结构、接口都不动。所以和"减轻耦合"完全不冲突——**两件事可以独立做，也可以都不做**。

---

## 四、这个方案能解决什么、不能解决什么

| 问题 | 是否解决 | 原因 |
| --- | --- | --- |
| **Via 主页显示源码** | ⚠️ **可能** | 取决于 WebView renderer 能否读 `/sdcard/Android/data/<容器>/`；Android 15 上概率较高 |
| **长按文字 ANR** | ⚠️ **可能同源** | 若主页加载正常了，长按的连带问题也可能消失 |
| **WebView 读 `/sdcard/Download`** | ✅ 无关（两种路径都能读外部存储） | |
| **Android 12 上无法运行 guest** | ❌ **不能** | 12 的 SELinux 策略更严，external 路径也可能 denied |
| **容器启动崩溃** | ✅ 无关 | p15 已解决 |

---

## 五、风险

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| **性能下降** | **中高** | `/sdcard` 是 FUSE，小文件 I/O 比 `/data` 慢 2–10 倍；guest 的 SQLite / 冷启动会明显变慢 |
| **数据迁移丢数据** | 中 | 切换前做 `moveDirectory`，或强制用户重装 guest |
| **外部存储权限** | 低 | 已有 `MANAGE_EXTERNAL_STORAGE`；`/sdcard/Android/data/<pkg>/` 在 Android 11+ 是应用私有 |
| **其他应用可读**（安全性） | **中** | `/sdcard/Android/data/<容器>/` 在 scoped storage 下其他应用**读不到**，但 **ADB/root 能读**，比 `/data` 更易泄露 |
| **Android 12 仍 denied** | 高 | 需要实测；若无效应回退到内部路径 |

---

## 六、建议的实施顺序

```
第 1 步（0.5 小时）：加开关 + external 路径，默认关闭
第 2 步（0.5 小时）：出包，用探针在容器内验证"能否创建/读取 external 沙箱"
第 3 步（关键）：装 Via，切到 external 沙箱，看主页是否正常渲染
       ├─ 正常 → 保留开关，做成"WebView 兼容模式"
       └─ 仍是源码 → 回退默认，把这个方向记为"已验证无效"
第 4 步（若有效）：再决定是否做成默认（权衡性能）
```

**第 3 步是决定性的**，只需要 10 分钟。**建议先做第 1-3 步，不急着做数据迁移。**

---

## 七、关于"为了 WebView 轻度改架构、减轻耦合，值不值"

**这个方案本身就不是"改架构"**：

| 对比 | 改架构 | 本方案 |
| --- | --- | --- |
| 改动量 | 抽接口、调整依赖、动 5+ 模块 | **1 个 Hilt provider + 1 个布尔开关** |
| 风险 | 高（回归面广） | 低（可开关、可回退） |
| 与耦合的关系 | 相关 | **无关** |

**所以结论更清楚了**：

> **为了 WebView 去改架构 → 不值**，因为本方案证明"支持 WebView"根本不需要动架构。
> **减轻 `VirtualEngine` 双向耦合 → 仍然值得，但那是独立的技术债**，和 WebView 无关，也不该混在一次变更里。

---

## 八、联网检索到的相关线索（供参考）

| 线索 | 说明 |
| --- | --- |
| **Android 11+ 对 WebView `file://` 更严格** | `setAllowFileAccess()` 默认 false；即使打开，也倾向限制在应用自己的目录 |
| **Android 11 下 WebView 无法加载应用专属目录 HTML**（多篇社区文章同题） | 与我们的现象吻合：路径"属于容器"但对 renderer 不可达 |
| **`/sdcard/Android/data/<pkg>/` 在 scoped storage 下是应用私有** | 其他应用读不到，但 ADB 能读 |
| **FUSE 性能** | 外部存储的小文件 I/O 明显慢于内部存储 |

**这些线索共同支持"Gaia 把沙箱放外部存储"这个推断**：它同时解决了"WebView 可见性"和"`/sdcard` 映射自然对齐"两个问题。
