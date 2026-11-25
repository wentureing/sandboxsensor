# 发现：MediaStore 查询被"放行"，但它绕过所有文件 hook

- 日期：2026-11-25
- 触发：查 via 的离线列表实现时，发现 `ha/e` 有两条列举路径；用户问「没处理 MediaStore 会怎么样」
- 状态：**已确认缺陷，未修**

---

## 一、via 的两条列举路径

反编译 `dist/viadex` 后，`ha/e.b` 是离线列表的入口：

```java
List<Item> list = new ArrayList<>();
list.addAll(e.f(dir));                                    // ← 路径 A：文件扫描
list.addAll(e.e(dir));                                    // ← 路径 B：MediaStore
Collections.sort(list, new ha.d());                       // 排序（不丢数据）
```

| 路径 | 方法 | 底层 |
| --- | --- | --- |
| **A** | `ha/e.f(String)` | `File.listFiles()` → 我们的 `list0` hook |
| **B** | `ha/e.e(String)` | `ContentResolver.query(content://media/external/file, ...)` |

`ha/e.e` 的查询参数（字符串常量实测）：

```
'_id', '_display_name', 'date_added', 'mime_type', '_size',
'relative_path like ?', '%', 'date_added DESC'
```

---

## 二、容器现在怎么处理 MediaStore

`core/binder/src/main/java/com/nextvm/core/binder/proxy/ContentResolverProxy.kt:327`：

```kotlin
if (authority != AUTHORITY_MEDIA) return QueryInterception.PassThrough

// For MediaStore queries, allow access but filter results to this instance
Timber.tag(TAG).d("MediaStore query from virtual app: $uri (instance=$instanceId)")

// Let it pass through — the native file hooks will handle sandboxing
return QueryInterception.PassThrough
```

**最后那句注释是错的。**

MediaStore 查询走 `ContentProvider` 的 **Binder IPC**，数据来自 `MediaProvider` 自己的数据库，
**完全不经过任何文件 syscall** —— 所以 **native file hooks（open/stat/opendir）根本拦不到它**。

---

## 三、三个后果

| # | 后果 | 说明 | 严重度 |
| --- | --- | --- | --- |
| **1** | **查不到沙箱文件** | 沙箱里的 `.mht`/`.pdf` 不在宿主 `MediaProvider` 的数据库里 → `query` 返回空 | 高 |
| **2** | **串味（隐私）** | 如果**宿主**的 `/sdcard` 里有 `.mht`/`.pdf`，guest 会看到**宿主的文件** —— `relative_path like '%'` 会匹配宿主路径 | **高** |
| 3 | **不会崩** | 实测日志无 MediaStore 异常，`query` 正常返回（只是结果不对） | — |

**第 2 条是安全缺陷**：guest 能看到宿主的媒体文件列表，这违反沙箱的基本预期。

---

## 四、对本轮"看不见"的影响

**不是直接原因**。因为 `ha/e.b` 是**两条路径合并**：

```
list.addAll(e.f(dir))    ← 文件扫描（我们已证明 list0 返回 2 条）
list.addAll(e.e(dir))    ← MediaStore（空或宿主数据）
sort
```

文件扫描的结果**应该**在列表里。所以 MediaStore 是**独立的正确性/安全缺陷**，
需要用单独一轮修（不是当前症状的根因）。

---

## 五、修的方向（未执行）

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| **A 过滤** | 对 `media` authority 的 query 返回**空 Cursor**（而不是放行） | 简单、安全；但 guest 就完全用不了 MediaStore（可能影响相册类应用） |
| **B 虚拟 Provider** | 在容器里实现一个虚拟 `MediaProvider`，把沙箱文件建成索引 | 正确但工作量大 |
| **C 直通 + 路径过滤** | 放行 query，但**改写 `relative_path` 条件**为沙箱路径 | 中等；依赖 selection 的字符串形态，脆弱 |

**建议先做 A**（最小可用、立刻消除串味），再评估 B。

**注意**：A 会让"依赖 MediaStore 的应用"拿不到数据，需要配合路径 A（文件扫描）一起判断
—— 如果应用只用 MediaStore 而不用 `File.listFiles`，那就会退化。实测需要看具体应用。

---

## 六、顺带说明

`ContentResolverProxy` 对 `settings` / `contacts` / `calendar` / `downloads` / `telephony`
都有具体处理，**只有 `media` 是明确放行的**（且放行理由写错了）。所以这是**遗漏**，不是设计。
