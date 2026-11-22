# 为什么商业软件不崩？以及"彻底更换 UI"的方案评估

- 日期：2026-11-22
- 背景：本机 Adreno 830 上，容器每遇到一个**新的 Skia shader 变体**就可能 `libllvm-glnext` abort

---

## 1. 核心规律：崩溃与「shader 变体数量」正相关，与"应用类型"无关

崩溃栈每次都长这样：

```
GrGLGpu::ProgramCache::findOrCreateProgramImpl
  → GrGLProgramBuilder::CreateProgram
    → GrGLCompileAndAttachShader        ← 编译一个新的 GL program
      → libllvm-glnext → abort
```

**关键点**：只有**首次创建某个 program 变体**时才编译。Skia 的 program cache 按 `GrProgramDesc`（着色器参数组合的哈希）索引。

于是：
- UI 效果种类越多 → program 变体越多 → 撞上驱动缺陷的概率越高
- 但一旦某个变体编译成功并缓存，后续复用就不再编译

### 已知的触发点（本机实测）

| # | Skia op | 来源 | 状态 |
|---|---|---|---|
| 1 | `FillRectOp`（阴影） | Material3 `ElevatedCard` elevation | p3 已去除 |
| 2 | 过渡动画 / 指示器 | `NavHost` fade + `NavigationBarItem` indicator | p5 已去除 |
| 3 | `ButtCapDashedCircleOp` | Material3 `CircularProgressIndicator`（drawArc） | p7/p8 已去除 |

---

## 2. 为什么商业软件（虚拟大师 / 平行空间 / 早期双开）不崩

| 维度 | 商业软件（2016–2020） | 我们的容器（2025 Compose） |
| --- | --- | --- |
| UI 体系 | **View（XML + findViewById）** | Jetpack Compose |
| 绘制原语 | `drawRect` / `drawBitmap` / `drawText` / `drawColor` | RenderNode + Skia 的完整 op 集 |
| 阴影 | 9-patch 图片（纹理，不是 shader） | `shadow`/`elevation` → 阴影 shader |
| 圆角 | 9-patch / `GradientDrawable`（CPU 或简单纹理） | `clip(RoundedCornerShape)` → mask/AA shader |
| 动画 | `Animation`/`ObjectAnimator`，少量属性 | Compose 无限动画、spring、transition |
| 进度指示 | 静态图片旋转 / 少帧序列 | `CircularProgressIndicator`（drawArc + 旋转） |
| 硬件加速 | 很多老应用甚至显式 `hardwareAccelerated=false` | 默认开启，且 Compose 强依赖 GPU |
| program 变体数 | **个位数** | **几十甚至上百** |

**结论**：
1. 商业软件**不是"更稳的实现"，而是"更少的绘制变体"**。View 体系的绘制指令是固定的一组，Skia 里对应少数几个 program，编译一次就复用。
2. 老应用常用 **9-patch 图片**代替阴影/圆角——那是**纹理**，不需要新 shader。
3. 部分老应用直接关掉硬件加速，等于永远不编译 shader。
4. 同一台设备上，系统设置（View 体系）我们实测显示 3 次全部正常，而 Compose 容器每次崩溃——**这就是最直接的对照**。

---

## 3. 彻底更换 UI：三种方案

当前 UI 规模（已统计）：

| 文件 | 行数 | @Composable |
| --- | ---: | ---: |
| FileManagerScreen.kt | 694 | 7 |
| AppManagerScreen.kt | 689 | 8 |
| LauncherScreen.kt | 587 | 8 |
| SettingsScreen.kt | 581 | 8 |
| PermissionDialogScreen.kt | 440 | 3 |
| 其他 8 个文件 | 889 | 12 |
| **合计** | **3880** | 47 |

Material3 使用：`Card` 7、`Scaffold` 5、`NavigationBar` 1、`Button` 7、`Icon` 58、`Text` 122。

| 方案 | 工作量 | shader 变体 | 崩溃风险 | 说明 |
| --- | --- | --- | --- | --- |
| **A. 换 View（XML 布局）** | **大**：3880 行重写 + 5 个模块 | **少**（固定原语） | 低 | 最接近商业软件形态，用 `RecyclerView` + 9-patch |
| **B. 换极简 Compose（自绘）** | **中**：约 1500–2000 行 | **极少**（自己控制） | 低 | 保留 Compose 但禁 Material3，只用 Box/Text/Image + 直角背景 |
| **C. 保留现状 + 逐个消除** | 小（已完成 3 轮） | 逐轮减少 | 中 | 每遇到新页面/新效果都要再修一次 |
| **D. 软件渲染兜底** | **极小**（1 行） | 0（不编译） | 最低 | 性能差（预计 <30fps），但绝对不崩 |

### 推荐：B + D（组合）

**B（极简 Compose）** 的理由：
- 保留现有代码结构与声明式写法，改造量比 A 小一半
- 可以精确控制"只用哪几种绘制原语"，从而把 program 变体压到个位数
- 不需要引入 XML/ViewBinding，也不用重学 View 生命周期

**D（软件渲染）** 作为可选兜底开关（按 GPU 型号），只在 B 仍不足时启用。

---

## 4. 方案 B 的「安全绘制原语」清单

| 允许 | 说明 |
| --- | --- |
| `Box` + `background(Color)` | 直角纯色背景 → `drawRect` |
| `Text` | 文本绘制（Skia 有独立缓存，风险低） |
| `Image(bitmap)` | 位图绘制 → `drawBitmap` |
| `Row` / `Column` / `Spacer` | 纯布局，不绘制 |
| 静态 `Icon`（矢量一次性渲染成 bitmap 并缓存） | 避免每次重绘矢量 path |

| 禁止 | 原因 |
| --- | --- |
| `shadow` / `elevation` / `Card` | 阴影 shader（已证实崩） |
| `clip(RoundedCornerShape)` 圆角裁剪 | mask/AA shader |
| `CircularProgressIndicator` / `drawArc` / 虚线 | `ButtCapDashedCircleOp`（已证实崩） |
| `NavHost` fade/slide 过渡 | 图层 alpha 合成 |
| `NavigationBar` indicator 动画 | 动画 + 圆角 mask |
| `ripple`（`indication = null` 关掉） | 水波纹需要额外图层 |
| `Modifier.alpha` 动画、`animateFloatAsState` 驱动绘制参数 | 频繁重绘 + 混合模式变体 |
| `blur` / `RenderEffect` | 需要离屏 framebuffer |

**替代做法**：
- 圆角 → 用 9-patch 或预渲染 PNG（纹理，不新增 shader）
- 阴影 → 用预渲染 PNG 或干脆不要
- 进度 → 静态文字（`"Loading…"`）或分段方块
- 图标 → 预渲染成 bitmap 并 `remember` 缓存（已在 p4 做过）

---

## 5. 分阶段落地建议

```
阶段 1（1 个页面验证，半天~1 天）
    只重写 LauncherScreen 为极简 Compose
    判据：连续启动 10 次 + 滚动 20 次，0 崩溃

阶段 2（其余页面，2~3 天）
    AppManager / Settings / FileManager / 对话框
    统一用一套「安全组件库」：
      SafeCard（直角背景，无 elevation）
      SafeIcon（预渲染 bitmap + remember）
      SafeButton（无 ripple）
      SafeDivider（1px Box）
    把这个库写进 AGENTS.md，禁止直接用 Material3 组件

阶段 3（可选兜底）
    按 GPU 型号检测，提供"兼容模式"开关 -> 全局 LAYER_TYPE_SOFTWARE
```

---

## 6. 风险与诚实交代

1. **不能保证 0 崩溃**：即使极简，Skia 内部的文本渲染、位图格式转换也可能产生新的 program 变体；只是概率会大幅下降。
2. **UI 观感会变朴素**：没有圆角、没有阴影、没有过渡动画。这是"在缺陷驱动上求稳定"的必然代价。
3. **工作量真实存在**：3880 行 Compose 重写，按方案 B 约 1500–2000 行，需要逐个页面回归。
4. **换 View 体系（方案 A）不能免俗**：View 也能用 `elevation`/`outlineProvider`/动画，如果用了同样会崩；只有"只用固定原语"才安全。
