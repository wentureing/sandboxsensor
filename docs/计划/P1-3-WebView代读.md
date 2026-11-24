# P1-3：WebView 代读（**暂不执行**）

> 原 §五。**用户已决定：等 Gaia 机制研究后再做。**
> 前置结论见 `../调研/调研/调查-导入代替代读的攻防.md` 第一节（`file://android_asset` vs `file://sdcard`）。


### 5.1 **前置验证（必须先做，否则可能白做）**

探针 v1.8：把最小 HTML 写进自己的 `getFilesDir()`（落在容器沙箱内），
输出绝对路径；然后在 Via 里打开那个路径。

| 结果 | 结论 |
| --- | --- |
| 正常渲染 | renderer 能读内部存储 → 方向要改 |
| **显示源码/失败** | **证实需要宿主代读** |

### 5.2 最小版范围

```
✅ file:/ 的 html / css / js / png / jpg / svg
✅ 补 Content-Type + Content-Length
❌ 不做 range（视频）、不做缓存、不做 content:/
```

覆盖约 80% 的"本地页"场景。

### 5.3 模块与拦截点

```
core/webview/
├── build.gradle.kts
└── src/main/java/com/nextvm/core/webview/
    ├── HostReadPolicy.kt      ← 从 P1-2 迁入
    ├── MimeTable.kt           ← 扩展名 → MIME
    └── FileSchemeInterceptor.kt  ← 生成 WebResourceResponse
```

**拦截点的现实困难**（必须诚实记录）：

| 想 hook 的 | 问题 |
| --- | --- |
| `WebView.setWebViewClient` | framework 方法，容器 `VirtualClassLoader` 插不进去 |
| View 树遍历找 `WebView` | 不可靠（找不到、时机不对） |

**所以 P1-3 的第一步不是写代码，是"找可行的注入点"** —— 这也是为什么它排在最后。

### 5.4 失败回退

如果找不到注入点，`HostReadPolicy` + `MimeTable` 仍然有价值（可以给"容器自己的 WebView"用），
但 Via 这类第三方 guest 仍然无解。**这种情况要把结论写进已知边界，不再投入。**

---