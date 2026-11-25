# 计划：动态代理转交 + AOSP 源码获取规范

- 日期：2026-11-25
- 状态：**计划，暂不执行**（用户明确：先解决"能不能运行"）
- 关联：`docs/架构/架构-为什么hook必须仿真而非转交.md`

---

## 一、动态代理转交（P0 计划，未执行）

### 1.1 要解决的问题

当前 `libcore.io.Linux.*` 用 `RegisterNatives` **替换**，被迫重写 AOSP 的包装层行为
（失败抛什么异常、返回什么值）。这类"仿真"每一次版本变化都可能出错。

而 `libcore.io.Os` 是 **public interface**（实测 flags = `PUBLIC INTERFACE ABSTRACT`），
`libcore.io.Libcore.os` 是 **public 字段**（`PUBLIC FINAL`），
**可以用 Java 动态代理替换它** —— 只翻路径，其余全部转发给原 `Os`。

### 1.2 设计要点（未实施）

```
原链路：android.system.Os.mkdir(p, mode)
          -> Libcore.os.mkdir(p, mode)      <- 反射替换这里
            -> BlockGuardOs.mkdir(p, mode)
              -> Linux.mkdir(p, mode)  [native]

替换后：android.system.Os.mkdir(p, mode)
          -> libcore.io.Os proxy.mkdir(p, mode)
               args 里的 String 路径 -> translatePath()
               return method.invoke(originalOs, args)   <- 转交，不仿真任何行为
```

### 1.3 为什么这样就没有仿真负担

| 项目 | RegisterNatives（现在） | 动态代理（计划） |
| --- | --- | --- |
| 失败时抛什么异常 | **我们实现**（易错） | 原 `Os` 决定 |
| 返回值语义 | **我们实现** | 原 `Os` 决定 |
| EINTR 重试 | **我们实现** | 原 `Os` 决定 |
| 方法增删 | 签名对不上就 RegisterNatives 失败 | Proxy 自动适配（方法名相同即可） |
| 可回退 | 需反向 RegisterNatives | 把 `os` 字段换回原值 |

### 1.4 关键前置问题（实施前必须解决）

| # | 问题 | 验证方法 |
| --- | --- | --- |
| 1 | `Libcore.os` 是 `public static final Os os`，能否反射改 | 反射 `Field.setAccessible(true)` + 去 final；Android 有 hidden API 限制，需实测 |
| 2 | 哪些路径参数需要翻译（`String` / `FileDescriptor` / `byte[]`） | 按方法签名分类：路径型 `String` 才翻译 |
| 3 | 返回值里的路径需要反向翻译吗（如 `readlink` 返回 `String`） | AOSP `Os.readlink` 返回目标路径 → **必须反向翻译**，否则拿到沙箱路径 |
| 4 | Proxy 开销 | `Os` 调用不密集（实测 `Os` 只有 5–13 次/app），可接受 |
| 5 | **覆盖不到 `File.*`** | `File` 走 `UnixFileSystem`，**不经过 `Libcore.os`** → 这条仍需 `RegisterNatives` |

### 1.5 预期收益与局限

| | |
| --- | --- |
| **收益** | `android.system.Os.*` 那一侧彻底零仿真；版本适配几乎免费 |
| **局限** | **`File.*` 覆盖不到** —— 实测 app 的 `File` 调用量是 `Os` 的 100–400 倍，所以收益主要在"减少出错面"，不在"提高覆盖率" |

**结论**：值得做，但**优先级低于"让 File 那条路稳定运行"**。

---

## 二、AOSP 源码获取规范（**先看列表再下载**）

用户要求：寻找源码应参考
- `https://android.googlesource.com/platform/manifest`
- LineageOS

**流程**：

```
1. 先看 manifest（列出所有仓库，不下载）
     https://android.googlesource.com/platform/manifest/+/refs/heads/main/default.xml?format=TEXT
     （base64，解码后用 <project name="platform/libcore" path="libcore"> 定位）

2. 确认目标仓库与分支
     AOSP:      platform/libcore  (分支如 android-15.0.0_r*)
     LineageOS: LineageOS/android_libcore (分支 lineage-21.0 / lineage-22.x)

3. 才下载 —— 只取需要的文件，不 clone 整仓
     优先：git show HEAD:<path>  （配合 --depth 1）
     备选：raw.githubusercontent.com（本机 web_fetch 对它失败，需注意）
```

### 2.1 本轮实测的环境限制

| 方式 | 结果 |
| --- | --- |
| `web_fetch` → `android.googlesource.com` | ❌ `fetch failed` |
| `web_fetch` → `raw.githubusercontent.com` | ❌ `fetch failed` |
| `web_fetch` → `api.github.com` | ✅ **可用**（能读 `contents/`、拿到 base64） |
| `curl.exe` | ❌ `SEC_E_NO_CREDENTIALS`（沙箱 TLS） |
| `git -c http.sslBackend=openssl clone --depth 1` | ✅ **可用** |
| `aosp-mirror/platform_libcore` | ❌ **不存在**（404） |
| **`LineageOS/android_libcore`** | ✅ **可用**（clone 成功，分支 `lineage-21.0`） |
| `aosp-mirror/platform_frameworks_base` | ✅ 存在 |
| `aosp-mirror/platform_manifest` | ✅ 存在（`default.xml` 101 KB） |

### 2.2 已落盘的源码（作为对照基准）

| 文件 | 行数 | 用途 |
| --- | --- | --- |
| `tools/tmp/libcore_io_Linux.cpp` | 2916 | `Linux.*` 的真实实现 |
| `tools/tmp/UnixFileSystem_md.c` | 572 | `UnixFileSystem.*0` 的真实实现 |

**建议**：确认稳定后移到 `tools/ref/`（不要把 `tools/tmp` 当长期目录）。

---

## 三、当前优先级（用户指定）

| 优先 | 事项 | 状态 |
| --- | --- | --- |
| **P0** | **解决"能不能运行"** —— via 在 `javaIo=1` 下能否正常启动 | **进行中**（等 CI `45552613793`） |
| P1 | 动态代理转交 | **计划，不执行** |
| P2 | 对照 AOSP 补 `canonicalize0`（失败抛 `IOException`） | 待做 |
| P3 | 把 AOSP 源码移入 `tools/ref/` | 待做 |
