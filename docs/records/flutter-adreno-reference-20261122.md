# 参考：Flutter 对 Adreno Vulkan 问题的处理演进

- 调查日期：2026-11-22
- 目的：评估「更新 Flutter 版本」能否缓解本项目的容器崩溃

---

## 1. 结论先行

**更新 Flutter 版本对本项目没有直接影响。** 容器是 Compose + Android HWUI(Skia)，与 Flutter 的 Impeller 是两套渲染栈。

但 Flutter 社区的处理**策略**值得照抄，而且他们的演进方向正好印证了我们的判断。

---

## 2. Flutter 侧的关键动作

| 时间/编号 | 内容 | 意义 |
| --- | --- | --- |
| [flutter/engine#54920](https://git.surdeus.su/core/flutter/commit/1018afbb9b837c7b3f61f865efc4bfe7e0655055) | **"disable Adreno 630 vulkan and add mechanism for android driver denylisting"** | 首次针对 Adreno 630 禁用 Vulkan，并建立**按 GPU 型号拉黑驱动**的机制 |
| [flutter/flutter#177747](https://github.com/flutter/flutter/pull/177747) | **"[Impeller] Fall back to OpenGL ES on older Adreno GPUs"** | 回退范围从单型号扩大到「旧 Adreno 系列」 |
| [Chromium GrVkGpu.cpp](http://codereview.chromium.org/2352083002/diff/1/src/gpu/vk/GrVkGpu.cpp) | "Workaround for Adreno INITIALIZATION_FAILED bug" | **Skia** 侧也有同类 workaround（HWUI 用的也是 Skia） |
| [horizon #15293](https://github.com/horizon-rust/horizon/issues/15293) | "Adreno on Vulkan: the device is lost while the pipelines are made" | 第三方引擎在同样位置（`vkCreateGraphicsPipelines`）失败 |

三条独立证据都指向同一结论：**Adreno 的 Vulkan 驱动在创建某些 pipeline 时会失败/abort**，上游项目的标准应对是**按 GPU 型号回退 GLES**。

---

## 3. 为什么 Flutter 的修复进不到我们的容器

| 层 | 我们的容器 | Flutter |
| --- | --- | --- |
| UI 框架 | Jetpack Compose | Flutter widgets |
| 渲染引擎 | **Android HWUI（系统 libhwui + 系统 Skia）** | **Impeller**（自带，随 app 打包） |
| Vulkan 使用 | 由系统 HWUI 决定 | 由 Impeller 决定 |
| 修复可否随 app 更新 | ❌ 不能（系统库） | ✅ 能（引擎随 app） |

我们的崩溃栈是 `libhwui.so → vulkan.adreno.so → libllvm-glnext.so`，**libhwui 是系统库**，任何 Flutter 版本的改动都不会影响它。

Skia 是共享的，但 Android 15 的系统 Skia 里那条 Adreno workaround 显然**没覆盖我们的 pipeline 配置**（否则不会 abort）。

---

## 4. 对我们的可迁移做法

| 做法 | 对应到 Android/Compose |
| --- | --- |
| 按 GPU 型号 denylist | 运行时读 `Build.HARDWARE` / `GL_RENDERER`，决定是否用软件层 |
| 回退 OpenGL ES | `debug.hwui.renderer=opengl`（全局，需验证生效）或单 View 用 `setLayerType(LAYER_TYPE_SOFTWARE)` |
| 减少 pipeline 变体 | 已在做：移除 Card 阴影（p3）、缓存 icon bitmap（p4） |

---

## 5. 对当前修复的启示

Flutter 的路径说明：**这类问题无法靠"少用某一种效果"彻底根治**，最终都要落到「按设备能力选择后端」。

因此建议：
1. 先做低成本优化（p3 阴影、p4 图标缓存），把崩溃率压到可接受
2. 同时准备**运行时 GPU 检测 + 回退**的开关（对应 Flutter 的 denylist）
3. 不指望通过库版本升级解决（系统 HWUI 不受我们控制）
