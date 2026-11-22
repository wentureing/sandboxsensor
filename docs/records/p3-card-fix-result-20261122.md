# p3 验证：Card 阴影移除后，崩溃率 67% → 11%

- 日期：2026-11-22 17:22–17:26（设备时间）
- 容器：`e7f68af`（含 `294a2a9` 的 Card 阴影移除）
- 设备端日志：`/data/local/tmp/cap-172049.log` → `probe-reports/cap-*.txt`（4.8 MB / 26,327 行）
- 探针：`com.sandbox.probe_1795339460851`

---

## 1. 结果

| 指标 | 修复前 | 修复后（本次） |
| --- | --- | --- |
| 点击启动崩溃率 | **2/3 ≈ 67%** | **1/9 ≈ 11%** |
| `am_crash` | 每次点击必现 | 全程 2 次（其中 1 次是**装新容器前**旧版本的 17:21:35） |
| `Displayed` 成功 | 极少 | **9 次** |
| ANR | — | 0 |

新容器（17:22:39 安装）之后只有 **1 次崩溃（17:25:33）**：

```
17:25:33.340  am_crash: [2736,0,com.nextvm.app,553139782,Native crash,Aborted,unknown,0,0]
栈：libc abort ← libllvm-glnext ← vulkan.adreno ← libhwui
```

**结论：修复有效（降 6 倍），但未根治，崩溃栈完全相同。**

---

## 2. 崩溃前的操作节奏

```
17:25:30.189  am_proc_start com.nextvm.app (preStart_down)      ← 用户点击
17:25:30.508  Displayed MainActivity +272ms                     ← 成功
17:25:30.988  MainActivity top_resumed_lost                     ← 0.5s 后被切走
17:25:30.989  MainActivity performPause
17:25:31.031  MainActivity STOP
17:25:31.744  am_proc_start com.nextvm.app (preStart_up)        ← 再次点击
17:25:31.988  Displayed +?                                      ← 成功
17:25:33.340  am_crash （RenderThread SIGABRT）                  ← 1.4s 后崩
```

**崩溃出现在「短时间连续启动/切换」之后**，不是首次冷启动。这与"pipeline 缓存/纹理反复创建"的特征一致。

---

## 3. 新的高优先级嫌疑：`toBitmap()` 没有缓存

`LauncherScreen.kt`（修复前 L290-295，修复后同）：

```kotlin
if (app.icon != null) {
    Image(
        bitmap = app.icon!!.toBitmap(128, 128).asImageBitmap(),   // ← 每次重组都重新栅格化
        ...
    )
}
```

- `toBitmap()` 是**在组合阶段同步执行的昂贵操作**，没有 `remember` 缓存
- 每次重组（滚动、状态变化、快速切换）都会**新建 Bitmap → 新纹理上传**
- 叠加 p1 前观察到的 `icon=AdaptiveIconDrawable`（多图层合成，成本更高）
- 纹理反复创建 + 快速 UI 切换，与 Adreno 在 `vkCreateGraphicsPipelines` 处的缺陷叠加，可能仍触发 abort

**修复**：`remember(app.instanceId) { app.icon.toBitmap(128,128).asImageBitmap() }`，把栅格化结果缓存到 app 级别。

---

## 4. 剩余可选方案（按顺序）

| # | 方案 | 预期 |
| --- | --- | --- |
| **B** | `toBitmap` 加 `remember` 缓存 | 消除重复栅格化与纹理创建，可能一并解决剩余 11% |
| **C** | 去掉 `combinedClickable` 的 press elevation / ripple 动画 | 减少动画期间的 pipeline 变体 |
| **D** | MainActivity `android:hardwareAccelerated=false` | 彻底绕开 Vulkan（UI 变慢） |
| **E** | 按 GPU 型号 denylist（学 Flutter） | 工程化兜底 |

建议先做 **B**（代码 1 行，同时解决性能问题），再做 C。
