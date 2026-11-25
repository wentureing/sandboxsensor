# 记录：via 离线保存 —— 保存成功但读不到（缺陷机制已定位）

- 日期：2026-11-25
- 触发：用户允许反编译 via，寻找离线保存路径
- **纠正**：上一版记录（`记录-via保存离线网页静默失败.md`）结论有误 ——
  实际**保存完全成功**，缺陷在"**读的路径与写的路径不同**"

---

## 一、保存其实成功了

沙箱里三个完整的 MHTML（`<Saved by Blink>`）：

```
<instance>/sdcard/Android/data/mark.via/files/offline/
  搜索 - Microsoft 必应.mht   478260 B  11:07
  百度一下.mht                578648 B  11:07
  百度一下 (1).mht            578648 B  11:09   ← 正是用户操作的时刻
```

文件头：
```
From: <Saved by Blink>
Snapshot-Content-Location: https://www.baidu.com/
Subject: =?utf-8?Q?=E7=99=BE=E5=BA=A6=E4=B8=80=E4=B8=8B?=   ("百度一下")
MIME-Version: 1.0
```

→ **写入路径正确、内容完整、时间对得上**。

## 二、但列表读的是另一个地方

| 路径 | 内容 |
| --- | --- |
| **宿主** `/storage/emulated/0/Android/data/com.nextvm.app/files/offline/` | **空** |
| 宿主同级的 `filters` / `font` / `gm` / `logs` / `proxy` / `syncing` / `tabs` | **全部空**（`find -type f` 无结果） |
| **沙箱** `<instance>/sdcard/Android/data/mark.via/files/offline/` | **3 个 .mht** ✓ |
| 沙箱 `<instance>/external_files/` | **空** |

## 三、缺陷机制（核心结论）

> **via 的 `getExternalFilesDir()` 在不同调用时刻返回了两个不同的根。**

| 动作 | 用的根 | 落点 |
| --- | --- | --- |
| **建目录**（8 个子目录） | `/storage/emulated/0/Android/data/**com.nextvm.app**/files/` | **宿主**（空壳） |
| **写 .mht** | `/storage/emulated/0/Android/data/**mark.via**/files/` | **重定向到沙箱** ✓ |

**所以**：
1. 保存 = 写沙箱 → **成功**
2. 列表 = 列宿主 `offline/` → **空** → **看不到**

## 四、根因

`VirtualContext.getExternalFilesDir()` **返回沙箱路径**：
```kotlin
override fun getExternalFilesDir(type: String?): File {
    val dir = sandboxManager.getExternalAppFilesDir(instanceId, guestPackageName, type)
    return dir
}
```

但宿主目录 `com.nextvm.app/files/*` 被创建了 → **说明 via 至少有一处拿到的 `Context` 是宿主的**
（不是 VirtualContext）。

同时 v142 重定向规则只覆盖：
```
/storage/emulated/0/Android/data/$guestPackageName/   → 沙箱    // guestPackageName = mark.via
```
**不覆盖 `com.nextvm.app`** → 宿主路径不会被纠正 → 目录真的建在宿主。

## 五、反编译证据（dist/via.apk → classes.dex）

关键字符串：

| 字符串 | 含义 |
| --- | --- |
| `saveWebArchive` | **WebView 的 MHTML 保存 API** |
| `.mht` / `save.html` | 保存格式与默认名 |
| `offline` | 目录名 |
| `getExternalFilesDir` | 路径来源之一 |
| `getExternalStorageDirectory` / `DIRECTORY_DOWNLOADS` | 其它候选位置 |
| `DocumentFile` | SAF 兜底 |
| `save open tabs, cost: %dms` | tab 持久化日志 |

**保存链路**：
```
getExternalFilesDir("offline")
  → WebView.saveWebArchive(<path>/<title>.mht)
  → 写 MHTML（异步回调）
```

## 六、为什么"看起来像静默失败"

用户视角：点了保存 → 没有"已保存"的条目 → 像是没成功。

**实际**：文件确实写了（沙箱），只是**列表扫的是宿主**，所以看不到。

**最坏的失败形态**：数据**没丢**，但用户**看不到**，且没有任何错误。

## 七、与「主页显示源码」的关系（更新）

| 症状 | 机制 | 是否同源 |
| --- | --- | --- |
| 主页显示源码 | WebView 拿到 `file://` 但按 `text/plain` 处理（W2） | — |
| 离线网页看不到 | **写沙箱、读宿主**（两个 Context 根不一致） | ❌ **不同源** |

**上一版记录里"可能同源"的判断要撤回** —— 这是**路径根不一致**，与 MIME 无关。

## 八、如果要修（不在本轮范围）

| 方案 | 做法 |
| --- | --- |
| A | 让 `getExternalFilesDir` 始终返回 guest 路径（找出 via 哪个 Context 是宿主的） |
| B | v142 规则增加 `/storage/emulated/0/Android/data/com.nextvm.app/` → 沙箱<br>（但这会把容器自己的 external 也重定向，需谨慎） |
| C | 把宿主 `com.nextvm.app/files/*` 做成指向沙箱的符号链接（不可行：Android/data 不允许） |

**A 最对症**：需要先定位"via 从哪里拿到宿主 Context"。

---

## 九、附：dist 目录已清理

- 清理前 679 MB → 清理后 240 MB（释放 439 MB）
- 删除了：`mt*`/`cb*` 日志包与解压目录、旧容器 APK（v138–v146 等 8 个）、
  `framework-unzip`/`gaia-unzip`/`core*unzip`/`via-unzip`/`webview-unzip`、失败样本
- 保留：最新容器 APK、`via.apk`（反编译源）、`debug.keystore`、`via-db2`（数据库）、
  `framework.jar`/`webview.apk`（AOSP 对照）
