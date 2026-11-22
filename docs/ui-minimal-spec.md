# 极简 UI 规范（零装饰）

- 决策日期：2026-11-22
- 决策来源：**用户明确接受「没有任何过渡效果和圆角」**，美观要求低，稳定性优先
- 背景：Adreno 的 `libllvm-glnext` 在每个新的 Skia program 变体上可能 abort（见 `render-engine-investigation-20261122.md`）

---

## 1. 核心原则

> **只使用能映射到「固定少数几个 shader」的绘制原语。**
> 任何"看起来更漂亮"的效果都会新增 program 变体，也就是新增崩溃风险。

### 允许（白名单）

| 原语 | 对应绘制 | 备注 |
| --- | --- | --- |
| `Box(Modifier.background(color))` | `drawRect`（直角） | 唯一允许的背景 |
| `Text` | 文本 | Skia 有独立缓存，风险低 |
| `Image(bitmap)` | `drawBitmap` | 图标必须预渲染 + `remember` |
| `Row` / `Column` / `Spacer` / `padding` / `size` | 纯布局，不绘制 | 无风险 |
| `Divider` 自绘 1dp `Box` | `drawRect` | 不要用 Material3 `Divider` |

### 禁止（黑名单）

| 禁止项 | 崩溃证据 |
| --- | --- |
| `Card` / `ElevatedCard` / `elevation` / `shadow` | p3：`FillRectOp` 阴影 shader |
| `NavHost` 的 fade/slide 过渡 | p5：图层 alpha 合成 |
| `NavigationBar` / `NavigationBarItem` 的 indicator 动画 | p5 |
| **`CircularProgressIndicator` / `drawArc` / 虚线** | p7/p8：`ButtCapDashedCircleOp` |
| `clip(RoundedCornerShape)` 圆角裁剪 | mask/AA shader |
| `ripple`（`clickable` 默认指示） | 水波纹图层 |
| `Modifier.alpha` 动画、`animateFloatAsState` 驱动绘制参数 | 频繁重绘 + 混合变体 |
| `blur` / `RenderEffect` / `graphicsLayer` 的 scale/rotation | 离屏合成 |

---

## 2. 安全组件库（唯一允许的 UI 积木）

放在 `core:designsystem`，全项目**只准用这些**，不准直接用 Material3 组件。

```kotlin
/** 直角纯色卡片：替代 Card / ElevatedCard */
@Composable
fun SafeCard(
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.surface,
    content: @Composable ColumnScope.() -> Unit
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(color)          // drawRect，无 elevation、无圆角
            .padding(10.dp),
        content = content
    )
}

/** 直角纯色按钮：无 ripple、无 elevation */
@Composable
fun SafeButton(
    text: String,
    modifier: Modifier = Modifier,
    onClick: () -> Unit
) {
    Box(
        modifier = modifier
            .background(MaterialTheme.colorScheme.primary)
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null,                  // 明确关掉 ripple
                onClick = onClick
            )
            .padding(horizontal = 16.dp, vertical = 10.dp)
    ) {
        Text(text, color = MaterialTheme.colorScheme.onPrimary)
    }
}

/** 预渲染并缓存的图标：避免每次重组重新栅格化 / 重新走矢量 path */
@Composable
fun SafeIcon(
    drawable: Drawable?,
    sizeDp: Int,
    key: Any,
    modifier: Modifier = Modifier
) {
    val bmp = remember(key) { drawable?.toBitmap(sizeDp, sizeDp)?.asImageBitmap() }
    if (bmp != null) {
        Image(bitmap = bmp, contentDescription = null, modifier = modifier.size(sizeDp.dp))
    } else {
        Box(modifier.size(sizeDp.dp).background(MaterialTheme.colorScheme.surfaceVariant))
    }
}

/** 静态"加载中"：纯文本，无动画 */
@Composable
fun SafeLoading(text: String = "Loading…") {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(text, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** 1dp 分隔线 */
@Composable
fun SafeDivider() {
    Box(Modifier.fillMaxWidth().height(1.dp).background(MaterialTheme.colorScheme.outlineVariant))
}
```

---

## 3. 改造清单（按风险优先级）

| 优先级 | 文件 | 行数 | 主要问题 |
| --- | --- | ---: | --- |
| **P0** | `LauncherScreen.kt` | 587 | 启动首帧；`CircularProgressIndicator` ×2、`Card` ×2 |
| **P0** | `MainActivity.kt` | 162 | `Scaffold` + `NavigationBar`（p5 已部分处理） |
| **P1** | `AppManagerScreen.kt` | 689 | `CircularProgressIndicator` ×2、`Card` |
| **P1** | `SettingsScreen.kt` | 581 | `Card` |
| **P2** | `FileManagerScreen.kt` | 694 | `CircularProgressIndicator` ×1、`Modifier.shadow(8.dp)` |
| **P2** | `PermissionDialogScreen.kt` | 440 | 对话框（`Dialog` 会建独立窗口 + 阴影） |
| **P3** | 其他 6 个文件 | 889 | 列表项、图标、面包屑 |

**对话框特别说明**：`AlertDialog`/`Dialog` 在 Compose 里创建**独立 Window**，带默认阴影/圆角，风险很高。
建议改为**页面内覆盖层**（`Box` 全屏半透明 + 居中 `SafeCard`），不用 `Dialog`。

---

## 4. 验收标准

每个页面改造完成后：

| 项 | 标准 |
| --- | --- |
| 启动 | 连续启动 10 次，0 崩溃、10 次都有 `Displayed` |
| 页面切换 | 底部三栏各点 10 次，0 崩溃 |
| 列表 | 滚动 20 次，0 崩溃 |
| 卡片 | 点击 10 次，0 崩溃 |
| 回归 | `am_crash` 计数 = 0，无 `libllvm-glnext` 帧 |

> 判据统一用 `adb shell logcat -d -b crash` + `am_crash` 计数。

---

## 5. 与"软件渲染兜底"的关系

零装饰能把 program 变体压到个位数，但**不能保证 0 崩溃**（文本渲染、位图格式转换仍可能产生新变体）。

因此保留兜底开关（按 GPU 型号启用）：

```kotlin
// 检测到高风险 GPU 时，对根 View 用软件图层
if (Build.HARDWARE.contains("qcom") && glRenderer.contains("Adreno 8")) {
    window.decorView.setLayerType(View.LAYER_TYPE_SOFTWARE, null)
}
```

优先级：**先做零装饰（性能不变），不够再上软件渲染（性能代价）**。
