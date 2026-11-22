# 费效分析：完全移除动画 vs 更换渲染器

- 日期：2026-11-22
- 背景：崩溃根因是 Adreno 的 `libllvm-glnext`（GL/VK 共用 shader 编译器）在**创建新的 pipeline 变体**时 abort

---

## 0. 先明确约束（决定了可行域）

| 事实 | 含义 |
| --- | --- |
| `debug.hwui.renderer=skiagl` 已生效，栈从 `GrVkPipeline` 变 `SkiaOpenGLPipeline` | 换 **Vulkan↔GLES** 无效 |
| `libllvm-glnext.so` 是 Adreno 的 **GLES** 编译器（glnext = GL Next） | 两个后端共用同一编译器 |
| `debug.hwui.renderer=opengl` 也是 Adreno GL 栈 | 同样无效 |
| 只有**软件渲染**完全不经过 GPU 编译 | 唯一"彻底"选项 |

→ **"更换渲染器"在 GPU 加速范畴内没有第二条路**，只剩下"是否退回软件渲染"。

---

## 1. 方案 A：完全移除动画 / 阴影 / 图层

### 已实施与效果

| 补丁 | 内容 | 对崩溃率的影响 |
| --- | --- | --- |
| p3 | 移除 6 处 `ElevatedCard` 阴影 + `cardElevation(1.dp)` 归零 | **67% → 11%**（-56 个百分点） |
| p4 | Launcher 的 `toBitmap` 加 `remember` 缓存 | 待验证 |
| p5 | `NavigationBar.tonalElevation=0`、`indicatorColor=Transparent`、`NavHost` 过渡改 `None` | 待验证（用户已定位到点底部栏崩） |

### 好处

| 项 | 说明 |
| --- | --- |
| **直击根因** | 崩溃只在"新 pipeline 变体"时发生，少一种效果 = 少一批变体 |
| 零运行时开销 | 不增加 CPU/内存，不降低帧率（反而更快） |
| 无兼容性风险 | 不依赖设备能力，不需要机型判断 |
| 改动可增量 | 每处 3–10 行，可单独验证、单独回滚 |
| 已证明有效 | p3 单次就把概率砍到 1/6 |

### 代价

| 项 | 说明 |
| --- | --- |
| **UI 观感退化** | 无卡片阴影、无页面过渡、无导航指示器 → 显得"素"，但不影响功能 |
| **穷举成本** | Material3 里会生成 pipeline 的组件很多：`NavigationBar`、`FloatingActionButton`、`Snackbar`、`Dialog`、`BottomSheet`、`PullToRefresh`、`SwipeToDismiss`、`ModalDrawer`… 每一处都要排查 |
| **无法根治** | 以后新增任何带动画/阴影的 UI 都可能复现；属于长期约束（要写进 UI 规范） |
| 可能误伤 | 某些动画是可用性关键（如 loading 指示），去掉会影响用户感知 |

---

## 2. 方案 B：退回软件渲染

### 两种粒度

| 粒度 | 做法 | 影响范围 |
| --- | --- | --- |
| 全局 | manifest `<application android:hardwareAccelerated="false">` | 整个容器（含 guest） |
| 局部 | `view.setLayerType(LAYER_TYPE_SOFTWARE, null)` 或 Compose `Modifier.graphicsLayer` | 只影响指定 View/Composable |

### 好处

| 项 | 说明 |
| --- | --- |
| **彻底消除** | 完全不走 GPU 编译，理论上崩溃率 → 0 |
| 实现简单 | manifest 一个属性，或 1 行代码 |
| 可做兜底开关 | 配合 GPU 型号检测，只对问题机型启用（学 Flutter denylist） |

### 代价

| 项 | 说明 |
| --- | --- |
| **性能断崖** | Compose 全部走 CPU 绘制。1080×2376 屏幕上滚动列表、动画会明显掉帧（预计 <30fps，重绘面积大时更低） |
| 功耗上升 | CPU 软绘比 GPU 更耗电，发热更明显 |
| 部分功能失效 | `RenderEffect`（模糊）、部分 `graphicsLayer` 变换、视频/SurfaceView 叠加可能异常 |
| **guest 渲染受影响** | guest 的 WebView/游戏等本来就靠 GPU，宿主软绘不解决 guest 自身的 GPU 崩溃 |
| 不是"更换"而是"退化" | 从 GPU 加速退到 CPU，产品体验降级明显 |

---

## 3. 其他候选（都不可行或不划算）

| 方案 | 结论 |
| --- | --- |
| `skiavk`（Vulkan 后端） | ❌ 共用 `libllvm-glnext`，同样崩 |
| `opengl`（旧 GL 后端） | ❌ 同上 |
| 换 Skia 版本 | ❌ 系统库，改不了（除非定制 ROM） |
| 自绘 SurfaceView 绕开 HWUI | ❌ 工作量巨大，等于重写 UI 层 |
| 升级 Flutter/Compose | ❌ 编译器在驱动里，不在框架里 |

---

## 4. 对比表

| 维度 | A 移除动画 | B 软件渲染 |
| --- | --- | --- |
| 崩溃率预期 | 11% → 可能 <2%（需验证） | 0%（对宿主 UI） |
| UI 流畅度 | 不变（略快） | **严重下降** |
| 功耗 | 不变 | 上升 |
| 实现工作量 | 中（每处 3–10 行，需穷举） | **极小**（1 行） |
| 维护成本 | 高（新增 UI 要遵守规范） | 低（但永久性能损失） |
| 可否灰度 | 可以（逐处开关） | 可以（按机型） |
| 对 guest 的影响 | 无 | 宿主软绘不解决 guest 的 GPU 问题 |
| 风险 | 可能误伤关键动画 | 产品体验降级、部分效果失效 |

---

## 5. 推荐路线（组合，不是二选一）

```
第 1 步（已完成 80%）：移除动画/阴影/图层
    p3 Card 阴影      → 67% 降到 11%
    p4 icon 缓存      → 减少纹理/pipeline 变体
    p5 底部栏 + 过渡   → 直击用户报告的"点 Apps/Settings 崩"
    预期：压到 1~2%

第 2 步：建立「渲染规范」而非继续打补丁
    在 AGENTS.md / UI 规范里明确：禁用 elevated/shadow、禁用 NavHost 过渡动画、
    禁用 indicatorColor 动画、列表项图标必须 remember 缓存

第 3 步：把软件渲染做成「按机型的兜底开关」
    运行时检测（Build.HARDWARE / GL_RENDERER 含 "Adreno 8"）
    → 命中则对主 UI 用 LAYER_TYPE_SOFTWARE，或在设置里给"兼容模式"开关

第 4 步：不追求 0 崩溃
    11% 已被 p3 打到 1/6；剩下一两个点位用开关兜住即可，
    不必为了 0.5% 牺牲全部 GPU 性能
```

**一句话**：**A 是主路径（省性能、可增量），B 是兜底开关（保正确性、牺牲性能）**。两者都要，但默认走 A。

---

## 6. 待验证

p6（`935ff40` 触发）同时包含 p4 + p5。需要用户验证：

1. 启动 1–2 次（确认 p3 效果仍在）
2. **点底部三栏各 5 次**（p5 的靶点）
3. 进 Apps 列表滚动、点卡片
