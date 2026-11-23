# NEXTVM 架构图

本目录只**追踪 `.dot` 源码**，渲染产物（png/svg/pdf）不入库——随时可以重新生成。

## 图

| 文件 | 内容 |
| --- | --- |
| `core-classes.dot` | **核心类图**：12 个核心类，节点面积 ∝ 类行数，边宽 ∝ 引用次数，`A -> B` 表示 A 需要 B，虚线为回指（存在双向依赖） |
| `core-vs-edges.dot` | **核心 vs 边缘**：把核心折叠为一个整体，展示它与 UI 层（feature/*）、系统与服务（native-hook/binder/services/framework）、外部实体（Guest App / 探针 / Android Framework）的关系 |

## 重新渲染

需要 [Graphviz](https://graphviz.org/download/)。

```powershell
$dot = "C:\Program Files\Graphviz\bin\dot.exe"
cd docs\diagrams
& $dot -Tpng core-classes.dot   -o core-classes.png
& $dot -Tpng core-vs-edges.dot  -o core-vs-edges.png

# 矢量图（推荐用于文档嵌入）
& $dot -Tsvg core-classes.dot   -o core-classes.svg
& $dot -Tsvg core-vs-edges.dot  -o core-vs-edges.svg
```

## 数据来源

节点上的「行数 / 函数数」由 `tools/misc/collect-graph-data.py` 从源码统计：

```powershell
python tools/misc/collect-graph-data.py     # 输出 _ref/ShitJumpingVM/_graph.json
```

依赖关系来自「A 的源码里出现 B 的类名」的次数（`\b` 词边界匹配）。**这个统计会把注释和字符串里的提及也算进去**，所以页面上的数字应理解为「耦合强度」而非「编译期依赖」。真正的模块级依赖以 `build.gradle.kts` 的 `project(...)` 为准（见 `docs/records/记录-工程体检.md`）。

## 两张图各自说明什么

### core-classes.dot

```
VirtualEngine 处于中心，出度 11
  ├─ StubRegistry(7)               ← 最强
  ├─ ActivityThreadHook(6)
  ├─ VirtualResourceManager(5)
  ├─ SystemServiceProxyManager(4)
  ├─ VirtualApplicationManager(3)  ← 同时回指 VirtualEngine（双向）
  ├─ VirtualMultiProcessManager(3)
  ├─ VirtualWindowManager(3)
  ├─ NativeHookBridge(3)           ← 同时回指（双向）
  ├─ SandboxManager(3)
  ├─ PackageManagerProxy(2)        ← 同时回指（双向）
  └─ ActivityManagerProxy(1)       ← 同时回指（双向）
```

**读法**：`VirtualEngine` 是唯一的门面，其它类都通过它被访问；虚线回指说明存在「引擎调管理器，管理器回调引擎」的双向耦合——这是当前架构的主要耦合风险点。

### core-vs-edges.dot

**读法**：
- UI 层（5 个模块）全部指向核心（`持有引擎` / `安装卸载` / `配置` …），只额外共享 `core:designsystem`
- 核心分四路下沉到系统与服务：`native-hook.cpp`（GOT/PLT）、`core:binder`（AMS/PMS 代理）、`core:services`（VPMS/VAMS）、`core:framework`（APK 解析）
- 核心同时**向上拉起 guest**、**向外 hook Android 服务**
- 探针是独立 APK，跑在 guest 内，靠 Android API 观测核心行为
