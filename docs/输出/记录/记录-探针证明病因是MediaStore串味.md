# 探针实测：via 离线页面看不见的病因是 MediaStore 串味

- 日期：2026-11-25
- 工具：探针 F09–F14（`probe-19fc941`，装进容器 `com.nextvm.app` 内运行）
- 结论：**容器文件系统层完全正确；问题是 MediaStore 没有隔离，宿主 6477 条文件淹没了 guest 的 3 个 .mht**

---

## 一、用户观察到的关键区分

| 功能 | 数据来源 | 现象 |
| --- | --- | --- |
| 浏览历史 | SQLite `history` 表 | ✅ 能看到 |
| 标签页 | SQLite `tabs` 表 | ✅ 能看到 |
| **保存的网页（离线）** | **文件系统扫描 + MediaStore** | ❌ 看不到 |

**这个区分已经把问题锁在文件/内容链路上** —— 数据库功能正常。

---

## 二、探针实测结果（12 段 base64 报告，89 条用例）

### 全绿的（证明容器文件层没问题）

```
F09  offline dir write
     path=/storage/emulated/0/Android/data/com.nextvm.app/files/offline
     made=true mhtLen=33 txtLen=7

F10  via-filter replica          ← 复刻 ha/e.f 的六项判断
     dir.canRead=true dir.exists=true dir.isFile=false listFiles=2
     [probe-a.mht len=33 mtime=1795587605281 canRead=true isFile=true]
     [probe-b.txt len=7  mtime=1795587605281 canRead=true isFile=true]

F11  host-form list (via read side)   ← 用 getOpPackageName() 拼宿主形态
     opPkg=com.nextvm.app path=/storage/emulated/0/Android/data/com.nextvm.app/files/offline
     exists=true list=[probe-a.mht, probe-b.txt]
     [probe-a.mht len=33] [probe-b.txt len=7]

F13  mime/ext verdict (via ha/e.d)
     [probe-a.mht ext=mht mime=null isMht=false isPdf=false keep=true]
     [probe-b.txt ext=txt mime=null isMht=false isPdf=false keep=true]

F14  file uri chain (via ha/e.f)
     [uri=file:// path=.../offline/probe-a.mht exists=true len=33]
     [uri=file:// path=.../offline/probe-b.txt exists=true len=7]
```

**判读**：
- 写入侧能建目录、能写文件、长度正确
- **`getOpPackageName()` 拼出来的宿主形态路径与写入侧落到同一个目录**（F11 能看到 F09 写的文件）
- via 的六项判断（`canRead`/`exists`/`isFile`/`listFiles`/`length`/`lastModified`）全部返回真值
- `ha/e.d` 的 mht/pdf 判定不会丢弃条目（`mime=null` 时 `keep=true`）
- `Uri.fromFile -> getPath -> new File` 链路正常

### 唯一的异常

```
F12  MediaStore query (via ha/e.e)
     uri=content://media/external/file
     count=6477
     [com.nextvm.app size=3452 mime=null path=Android/media/]
     [probe-v113-33b8e4b.apk size=533637 mime=application/vnd.android.package-archive path=Download/]
     [v.xml size=8026 mime=text/xml path=/]
     [g4.xml size=3630 mime=text/xml path=/]
     [g3.xml size=7196 mime=text/xml path=/]
```

**6477 条，全是宿主的文件**（`Download/`、根目录 `/`、`Android/media/`）。

---

## 三、为什么这就等于"看不见"

via 的离线列表入口 `ha/e.b()`：

```java
List<Item> list = new ArrayList<>();
list.addAll(e.f(dir));                     // 文件扫描 → 沙箱里的 3 个 .mht  ✅
list.addAll(e.e(dir));                     // MediaStore  → 宿主的 6477 条    ❌
Collections.sort(list, new ha.d());        // 按 date_added 排序
```

**用户的 3 个 `.mht` 被夹在 6477 条宿主文件中间**，按时间排序后散落各处 —— 在 UI 上就是"看不见"。

> **修正之前的判断**：我曾把 MediaStore 归类为「独立的安全缺陷，与当前症状无关」。
> 探针数据表明它是**当前症状的直接原因**。

---

## 四、根因：`ContentResolverProxy` 对 media 明确放行且理由写错

`core/binder/src/main/java/com/nextvm/core/binder/proxy/ContentResolverProxy.kt:327`：

```kotlin
if (authority != AUTHORITY_MEDIA) return QueryInterception.PassThrough

// For MediaStore queries, allow access but filter results to this instance
Timber.tag(TAG).d("MediaStore query from virtual app: $uri (instance=$instanceId)")

// Let it pass through — the native file hooks will handle sandboxing
return QueryInterception.PassThrough
```

**最后那句注释是错的**：MediaStore 走 `ContentProvider` 的 Binder IPC，数据来自 `MediaProvider`
自己的数据库，**完全不经过文件 syscall**，native file hooks 拦不到。

`settings` / `contacts` / `calendar` / `downloads` / `telephony` 都有具体处理，
**只有 `media` 是明确放行** —— 属于遗漏。

---

## 五、修的方向

| 方案 | 做法 | 代价 | 风险 |
| --- | --- | --- | --- |
| **A 返回空 Cursor** | media authority 的 query 直接返回空 | 小 | 相册类应用拿不到数据（功能退化，但不再串味） |
| **B 过滤宿主路径** | 放行 query，但把 `relative_path` 结果里不属于该 guest 的记录剔掉 | 中 | 几乎全是宿主记录 → 效果等同 A |
| **C 虚拟 Provider** | 容器实现一个虚拟 MediaProvider，把沙箱文件建成索引 | 大 | 正确但工作量大 |

**建议先做 A**（立刻消除串味，也让 via 的列表只剩文件扫描的 3 条 → 直接验证病因），
再评估 C。

**A 的验证方法**：改完后重装，via 的离线列表应该只剩 `.mht` 条目（来自 `e.f`）。

---

## 六、附带确认的容器事实（J 系列）

```
J08  getExternalFilesDir              /storage/emulated/0/Android/data/com.nextvm.app/files
J13  Environment.getExternalStorageDirectory   /storage/emulated/0
J30  filesDir canonical vs absolute
     absolute=/data/user/0/com.nextvm.app/files/virtual/data/com.sandbox.probe_xxx/files
     canonical=/data/data/com.nextvm.app/files/virtual/data/com.sandbox.probe_xxx/files
```

- `getExternalFilesDir` 返回**宿主形态**（`/storage/emulated/0/...`），不是沙箱路径
  —— 这与 `setupAppRedirections` 的规则一致，F11 证明两侧映射正确
- `canonical` 把 `/data/user/0` 规范化成 `/data/data` —— 需要确认 `canonicalize0` 行为是否影响后续判断

---

## 七、工具

探针报告解码脚本：`tools/probe/decode-report.py`（抓 logcat 的 12 段 base64 → 拼 JSON → 按 F/J/P/N 分组打印）
