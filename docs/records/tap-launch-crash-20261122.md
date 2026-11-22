# 关键发现：**只有从 Launcher 点击才会崩**（am start / monkey 都不崩）

- 日期：2026-11-22 16:55–17:01
- 设备：vivo V2463A / Android 15 / Adreno
- 容器：ShitJumpingVM + p1（沙箱内有 YESPDF + 探针，状态"脏"）

---

## 1. 三组启动方式对比（同一容器、同一状态）

| 启动方式 | 命令 | 结果 |
| --- | --- | --- |
| `monkey` | `monkey -p com.nextvm.app -c LAUNCHER 1` | **5/5 成功**（pid 活、Displayed=1、crash=0） |
| `am start` | `am start -W -n com.nextvm.app/.MainActivity` | **3/3 成功** |
| **Launcher 点击** | `input tap 663 279`（NEXTVM 图标中心） | **2/3 崩溃**（第 3 次未起来，`crash=1`、`displayed=0`、`rt=1`） |

复现率约 67%，但**只有点击路径会崩**，与"重启"或"数据脏"无关。

---

## 2. 点击路径的事件序列（崩溃现场）

```
17:00:44.829  2736  5736 I wm_create_activity: [0,110991652,874,
                          com.nextvm.app/.MainActivity, android.intent.action.MAIN,
                          NULL, NULL, 807403520]          ← intent flags = 0x30200400
17:00:44.851 16087 16087 I wm_on_paused_called: [.., com.bbk.launcher2.Launcher, performPause, 0]
17:00:44.936  2070  2070 I wm_on_resume_called: [110991652, com.nextvm.app.MainActivity, RESUME_ACTIVITY, 0]
17:00:45.272 16087 16087 I wm_on_resume_called: [.., com.bbk.launcher2.Launcher, RESUME_ACTIVITY, 1]  ← 退回桌面
--------- beginning of crash
Fatal signal 6 (SIGABRT) in tid 2659 (RenderThread), pid 2070 (com.nextvm.app)
#00 libc abort+172
#01-#05 libllvm-glnext.so
#06-#09 vulkan.adreno.so
#10-#16 libhwui.so GrVkPipeline::Make
#17-#19 libhwui.so FillRectOpImpl::onExecute
```

**关键**：MainActivity 确实 `onResume` 了，然后在 ~340ms 内渲染第一帧时崩，Launcher 随即回前台 → 用户看到"白屏一闪就退出"。

---

## 3. 为什么 `am start` / `monkey` 不崩

差异在**启动上下文**，不在目标 Activity：

| 差异点 | Launcher 点击 | am start / monkey |
| --- | --- | --- |
| intent flags | `0x30200400`（含 SINGLE_TOP + NEW_TASK + RESET_TASK_IF_NEEDED） | 更简单 |
| ActivityOptions | Launcher 会带 clip-reveal / scale-up 过渡动画 | 通常无 |
| starting window | 有（白屏就是它） | 也会有，但过渡路径不同 |

**结论**：崩溃由「Launcher 启动带过渡动画 + starting window」这条渲染路径触发，某个基础 pipeline（`FillRectOp`）在该上下文里让 Adreno 的 shader 编译器 abort。

---

## 4. 顺带排除：不是权限问题

| 检查 | 结果 |
| --- | --- |
| 已授予权限 | 32 项 |
| 被拒绝 | 5 项（含 `SYSTEM_ALERT_WINDOW: ignore`、`MANAGE_EXTERNAL_STORAGE: default`） |
| 但 `am start` 能正常启动 | ✅ 8/8 |

→ **权限足以启动容器**。若权限不足，`am start` 同样会失败。可排除。

---

## 5. 修复候选（按验证顺序）

| # | 方案 | 改动 | 预期 | 风险 |
| --- | --- | --- | --- | --- |
| **A** | `MainActivity` 主题加 `android:windowDisablePreview=true` | 容器 styles.xml 1 行 | 消除 starting window；若崩溃由预览窗口渲染触发则解决 | 首帧前可能短暂黑屏 |
| **B** | `MainActivity` 加 `android:hardwareAccelerated=false` | manifest 1 行 | 软件渲染，不走 Vulkan | Compose UI 明显变慢 |
| **C** | `debug.hwui.renderer=opengl` | setprop | 走 GLES | 之前 `skiagl` 无效，需确认属性是否被框架读取 |
| **D** | 代码里 `window.setFormat(PixelFormat.TRANSLUCENT)` 或 `overridePendingTransition(0,0)` | Kotlin 1-2 行 | 绕过过渡动画路径 | 需确认崩溃点是否在动画 |

验证流程（每个候选）：

```
重建容器 → 安装 → 连续点击图标 5 次
  判据：crash=0 且 displayed=1 达到 5/5
  同时保留 monkey 3 次作为对照（不能回归）
```

---

## 6. 当前状态

- 容器处于"点击 2/3 崩"状态，但 `am start`/`monkey` 可用 → **自动化测试可以先用 `am start` 驱动**，不必依赖 Launcher
- 若最终目标包含"用户点图标也能用"，必须走 §5 的修复
