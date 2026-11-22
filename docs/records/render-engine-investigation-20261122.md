# 渲染引擎调查：确实是 OpenGL 了，但崩溃没消失（`libllvm-glnext` 是 GL 编译器）

- 调查日期：2026-11-22
- 设备：vivo V2463A / **Adreno 830** / GLES 3.2 `V@0800.46 (GIT@40e1860a19)`
- 数据来源：`probe-reports/cap-*.txt`、`tap-crash-165912.txt`、设备属性

---

## 1. 当前渲染栈（确认）

| 层 | 实际使用 |
| --- | --- |
| UI 框架 | Jetpack Compose（容器 `MainActivity` 用 `setContent`） |
| 绘制引擎 | Android **HWUI**（`/system/lib64/libhwui.so`） |
| 图形 API 后端 | **OpenGL ES**（不是 Vulkan） |
| 底层 | 系统 Skia → `SkiaOpenGLPipeline` |
| GPU | Qualcomm **Adreno 830** |

### 证据一：属性已生效

```
debug.hwui.renderer = skiagl        ← 之前设置的，确实生效了
ro.hardware.vulkan  = adreno
ro.hardware.egl     = adreno
```

### 证据二：崩溃栈里是 `SkiaOpenGLPipeline`，不是 Vulkan

17:00:45（真实点击测试）与 17:25:33（p3 之后）：

```
#22 SkiaPipeline::renderFrame(...)
#23 SkiaOpenGLPipeline::draw(...)+704        ← OpenGL 路径
#24 CanvasContext::draw(bool)+852
#25 DrawFrameTask::postAndWait()
```

**没有** `GrVkPipeline::Make`、**没有** `vkCreateGraphicsPipelines`。

---

## 2. 关键认知修正：`libllvm-glnext.so` 是 **GLES** 编译器

之前把 `libllvm-glnext.so` 当成 Vulkan 专属，是错的：

- `glnext` = **GL Next**，即 Adreno 的 OpenGL ES 着色器编译器
- Vulkan 路径（`vkCreateGraphicsPipelines`）和 GLES 路径（`SkiaOpenGLPipeline::draw`）**共用这个 LLVM 后端**
- 因此：

```
Vulkan 崩溃栈：libc abort ← libllvm-glnext ← vulkan.adreno ← GrVkPipeline
GLES   崩溃栈：libc abort ← libllvm-glnext ← (驱动) ← SkiaOpenGLPipeline
```

**换后端无法绕过，因为编译 shader 的是同一个库。**

这也解释了为什么 `debug.hwui.renderer=skiagl` 看起来"无效"——它其实**生效了**（栈从 Vulkan 变成 OpenGL），但崩溃依旧，因为缺陷在编译器本身。

---

## 3. 另一个独立问题：guest 进程的 EGL 初始化也崩

`cap-172410.txt` 里出现（注意进程是 **`:p0`**，不是主进程）：

```
F m.nextvm.app:p0: runtime.cc:717]
  #09 libhwui.so  EglManager::initialize()+2092
  #10 libhwui.so  RenderThread::requireGlContext()+92
  #11 libhwui.so  RenderThread::preload()+52
```

- 这是 **ART runtime abort**（`runtime.cc:717`），发生在 guest 进程 `RenderThread::preload()` → `requireGlContext()` → `EglManager::initialize()`
- 即：**guest 进程创建 EGL 上下文失败**
- 与主进程的 shader 编译崩溃是**两条独立故障**

推测原因：guest 进程与宿主进程共享 GPU 上下文/EGL 配置，在 `skiagl` 模式下 guest 进程的 EGL 初始化条件更严格。

---

## 4. 结论与可选路径

| 结论 | 说明 |
| --- | --- |
| 容器渲染栈 = Compose → HWUI → **SkiaOpenGLPipeline** | 不是 Flutter/Impeller，也不是 Vulkan |
| 崩溃根因 = Adreno 的 **GL/VK 共用 shader 编译器**（`libllvm-glnext`）缺陷 | 换后端无效 |
| 减少 shader 变体（p3 Card 阴影、p4 图标缓存）是对的方向 | 崩溃率 67%→11% 已验证 |
| guest 进程 EGL 初始化失败是另一条独立问题 | 尚未处理 |

### 剩余可选路径

| # | 方案 | 性质 |
| --- | --- | --- |
| **D（推荐）** | `android:hardwareAccelerated="false"` 或 Window `LAYER_TYPE_SOFTWARE` | **唯一彻底绕开 shader 编译器的方案**（软件渲染，UI 变慢） |
| E | 继续减少 shader 变体（p4 已在做） | 降低概率，不根治 |
| F | 换设备（非 Adreno 或驱动版本不同） | 环境规避 |
| G | 保留 `skiagl` + 加 GPU 型号 denylist（学 Flutter） | 工程化兜底 |

**注意**：`debug.hwui.renderer=opengl`（更老的 GL 后端）不解决问题——它同样走 `libllvm-glnext`。
