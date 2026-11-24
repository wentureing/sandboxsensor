# 记录：Java 层路径重定向打通（v141 + v142）

- 日期：2026-11-24
- 目标：补上架构缺口「Java 层的文件 I/O 完全没有重定向」
- 结论：**已打通**。`RegisterNatives` 替换 `libcore.io.Linux` + guest 进程补注册规则，
  不 patch libart、不引入外部框架。

---

## 一、为什么这件事重要

摸底的结论（`docs/架构/架构-路径转换机制摸底.md`）：容器只有两层——
`VirtualContext` 路径改写（只覆盖"通过 Context 拿路径"的代码）和 native GOT patch
（只覆盖被 patch 的 so）。**Java 层 `new File("/sdcard/x")` 是直通的**。

实测印证：探针 **W19** 用 Java `FileOutputStream("/sdcard/x")` → `EPERM`。

这条缺口是所有"假路径方案"的硬墙：
- 假 `dataDir`（v139）：framework 内部按 dataDir 拼路径 → 读不到
- `pathFidelity`：纯 Java 应用读合成路径 → 读不到
- WebView/MediaPlayer/PDF 这类"外部消费者" → 只能逐个打补丁

---

## 二、做法（两步，各一次构建）

### 第 1 步（v141）：`RegisterNatives` 替换 `libcore.io.Linux`

| 项 | 内容 |
| --- | --- |
| 位置 | `core/hook/src/main/cpp/native-hook.cpp` 末尾（照 `installNativeLoadHook` 的范式） |
| 替换的方法 | `open(String,int,int) → FileDescriptor`、`access(String,int) → boolean` |
| 实现 | 先 `redirect_path()` 翻译 → 再调真实 libc → 失败抛 `FileNotFoundException` |
| 开关 | `nextvm.javaIoRedirect`（默认 1） |
| 安全边界 | 只在 guest 进程安装（`initNativeHooks` 在主进程已 return，P0-3） |

**为什么不算 patch libart**：`RegisterNatives` 是 ART 公开的 JNI API，只改方法的 native
entry 指针，不修改 libart 代码段。

第一次实测：`installed=true` 但无效 → 诊断文件 `v141-javaio.log` 显示 **`rules=0`**。

### 第 2 步（v142）：在 guest 进程补注册重定向规则

**根因是架构缺口，不是 hook 本身**：

> 规则原先只在 `VirtualEngine.launch()`（**主进程**）注册，而 `NativeHookBridge`
> 的规则表是**进程级**的（`@Singleton`，每进程一份）→ guest 进程拿不到。

修复：在 `bindApplication` 里（framework + legacy 两条路径）调用
`setupExternalStorageRedirections` + `setupAppRedirections`，
把 `/sdcard/`、`/data/user/0/<guest>/` 等 11 条规则注册到 **guest 进程**。

---

## 三、验收结果（v142 容器 `1.0.0-d99049c`）

```
breadcrumb: v142-redirect registered rules=11
            appDir=.../com.sandbox.probe_1795530570750
            sdcard=.../com.sandbox.probe_1795530570750/sdcard

W38 Java 写 /sdcard OK -> /sdcard/wvprobe_javaio.txt
W38 Java 读回 OK: W38_JAVAIO_OK
W38 exists(/sdcard/wvprobe_javaio.txt) = true

文件实际落点：.../com.sandbox.probe_1795530570750/sdcard/wvprobe_javaio.txt（内容 W38_JAVAIO_OK）
```

**无回归**：W3 仍 `ASSET_OK`；W36/W37 正常；W2 仍 `text/plain`（未解决但未变坏）。

> ⚠️ 这条结论**不依赖 appops 授权**：干净安装会清掉 `MANAGE_EXTERNAL_STORAGE`，
> 但 W38 的判据是"路径被翻译到沙箱后才成功"，未翻译时必然失败 —— 所以判据有效。

---

## 四、意义

| 之前 | 现在 |
| --- | --- |
| Java 层写 `/sdcard` → `EPERM` | Java 层写 `/sdcard` → 落在沙箱 |
| 假路径方案全部被"读不到"卡死 | 假路径有了 Java 层兜底（native 层本来就有） |
| 每个"外部消费者"（WebView/MediaPlayer/PDF）都要单独打补丁 | 有了统一地基 |

**这也解释了 v139 为什么当时没用**：它只改了 dataDir，但那时 Java 层和 guest 规则两头都缺，
假路径必然读不到 —— 不是假设错，是**前置条件没到位**。

---

## 五、下一步候选（按 ROI）

| # | 项 | 说明 |
| --- | --- | --- |
| 1 | 扩到 `stat/lstat/readlink/unlink/rename/mkdir` | `Linux` 还有这些 native 方法；`stat` 最常用 |
| 2 | 重新评估"假 dataDir 让 WebView 走标准分支" | 现在 Java 层和 guest 规则都有了 → 前提具备（当初 v139 缺的就是这个） |
| 3 | 把 `pathFidelity` 推广到纯 Java 应用 | 之前卡在同一缺口 |
| 4 | `opendir` 重定向 | 目前只打日志，目录枚举仍不翻译 |

**注意**：第 2 项是"利用 WebView 内部实现细节"（C 层，寿命 2–3 个月）；
第 1/3 项是能力补齐（A/B 层，寿命长）。按 ROI 应先做 1，再评估 2。
