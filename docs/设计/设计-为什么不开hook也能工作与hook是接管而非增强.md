# 研讨：在 hook 不完善的状态下，为什么接手前的容器能完成多种功能

- 日期：2026-11-25
- 触发：用户提问 —— "在这种不完善状态为什么我接手前容器可以完成多种功能，尊重容器和实际系统的差异"
- 这个问题的答案，决定了我们**该不该继续加 hook**、以及**加到什么程度停**

---

## 一、核心答案：能工作，是因为**没开 Java hook**

### 1.1 关键事实

`DebugFlags.JAVA_IO` 的默认值是 **`false`**（v147 之后设的）。

**所以接手前的容器根本没装 Java 层 hook** —— `installJavaIoHooks()` 在 `nativeHooksAvailable`
之后返回 `false`，`nativeInstall` 从未被调用。

**那它靠什么工作？靠 `VirtualContext`。**

### 1.2 `VirtualContext` 才是主力（不是 hook）

```
app 代码
  │  context.getFilesDir() / getCacheDir() / getExternalFilesDir("xxx")
  ▼
com.nextvm.core.sandbox.VirtualContext
  │  直接返回 <sandbox>/... 绝对路径
  ▼
app 拿到的是沙箱路径
  │  new File(sandboxPath) / new FileInputStream(sandboxPath)
  ▼
libc 直接操作沙箱文件        ← 根本不需要 hook
```

**实测印证**：
- via 保存 `.mht` 成功（`<instance>/sdcard/Android/data/mark.via/files/offline/*.mht`）
- 这个成功**完全不是 hook 的功劳** —— 是 `getExternalFilesDir("offline")` 返回了沙箱路径

### 1.3 那 99% 的 app 怎么活的

实测 5 个 app 的 `File` 调用量（1380–2060 次），但**它们绝大多数都基于"从 Context 拿到的路径"**：

| 场景 | 是否需要 Java hook |
| --- | --- |
| `new File(getFilesDir(), "x.json")` | ❌ 不需要（路径已是沙箱） |
| `openFileOutput("x", MODE)` | ❌ 不需要（走 VirtualContext） |
| `getSharedPreferences(...)` | ❌ 不需要 |
| **`new File(Environment.getExternalStorageDirectory(), "Android/data/" + getOpPackageName() + "/...")`** | ✅ **需要** ← 这就是 via 的 `offline` 列表那条 |
| `File.length()` 判断文件非空 | ⚠️ 需要 `Linux.stat`（否则读宿主） |

**所以**：**容器的设计本来就是"用 Context 重定向为主、hook 兜底"**。
hook 只覆盖"应用自己拼路径"这种少数情况。

---

## 二、为什么"一开 hook 就废"：**hook 是接管，不是增强**

### 2.1 这是最关键的区别

```
不开 hook：
  File.open → libjava core → libc open(沙箱路径)      ← 原实现，一直是对的

开了 hook：
  File.open → libjava core → 我们的 hooked_java_linux_open
                                ↓
                             翻译路径 + 调 libc + **包装结果**
                                ↓
                             如果包装错了（异常类型/返回值），调用方崩
```

**`RegisterNatives` 是"替换"** —— 一旦替换，**所有**经过这条路的调用都走我们的代码，
包括那些**原本完全正常**的调用。

### 2.2 所以我们这几轮遭遇的所有崩溃，本质都是同一件事

| 轮次 | 崩溃 | 根因 |
| --- | --- | --- |
| v147 | Via 工具栏点不动 | `access` 失败返回 `false`（应抛 `ErrnoException`） |
| 本轮 | `FATAL: isSocket$() on null` | `open` 空参返回 null 不抛 + `ErrnoException` 类名写错 |
| 本轮 | `fstat failed: EBADF` | `Linux.fstat` 未 hook，`FileInputStream` 构造失败 |

**这三条都不是"guest 功能本身坏了"，而是"我们接管后包装错了"。**

> **不开 hook 时：这些路径走原实现，虽然路径可能没翻译（读到宿主），但不会崩。
> 开了 hook 后：路径翻译了，但包装错了 → 崩。
> 所以"不完善的 hook 比没有 hook 更危险"。**

---

## 三、"尊重容器和实际系统的差异"——这条原则具体是什么

### 3.1 容器的目标不是复刻 AOSP

| 复刻 AOSP（错的目标） | 让 guest 可用（正确的目标） |
| --- | --- |
| 每个 syscall 都要拦 | 只拦"应用自己拼路径"的情况 |
| 失败语义必须逐字节一致 | 失败语义只要**不破坏调用方假设** |
| 追求 100% 覆盖 | 追求"常见 app 不崩、功能可用" |

### 3.2 三档处理策略（按成本/收益）

| 情况 | 策略 |
| --- | --- |
| **VirtualContext 能覆盖**（Context 派生路径） | **不做 hook**（零风险、零成本） |
| **VirtualContext 覆盖不到，且应用会自己拼路径** | **必须 hook**，但要**逐项对齐 AOSP 的行为**（异常/返回值/边界） |
| **应用几乎不用的 API**（NIO2，实测 0–4 次 invoke） | **暂不 hook**（收益极低，风险照旧） |

**当前项目已经做的事，正好落在这三档里**：
- `getExternalFilesDir` → 第一档（VirtualContext）✅
- `File.listFiles`/`exists`/`mkdirs` → 第二档（必须 hook，已做 7 个 `*0` + 5 个 `Linux.*`）
- NIO2 → 第三档（暂缓）✅

### 3.3 由此得出的两条纪律

**纪律 1：hook 必须默认关闭，逐步启用**
- `JAVA_IO` 默认 `false` 是对的
- 每次只开一个新方法，验证通过再开下一个
- **不要"一次全开"** —— 那等于把所有缺陷同时释放

**纪律 2：每个 hook 上线前必须有"行为契约"验证**
- 签名：`jni-sig-audit.py`（设备 dex）
- 行为：对照 AOSP 源码（`UnixFileSystem_md.c` / `libcore_io_Linux.cpp`）
- 注册：`checks.log` 里的 `X/X registered`
- 真机：探针 + 真实 app

---

## 四、回到当前状态：现在的问题是什么

### 4.1 现在的组合是"最坏的情况"

```
JAVA_IO = true（开了）
  + 7 个 UnixFileSystem.*0 hook（多数已对齐 AOSP）
  + 5 个 Linux.* hook（部分行为未核对）
  + fstat / stat / lstat / canonicalize0 / getSpace0 缺失
```

**开了 hook 但没补全** → **比不开更糟**。

### 4.2 最小可运行集合（该补到什么程度才能"开"）

按本次实测的崩溃链，**至少需要**：

| # | 必补 | 理由 |
| --- | --- | --- |
| 1 | `Linux.fstat` | `FileInputStream` 构造（**当前阻塞点**） |
| 2 | `Linux.stat` | `UnixFileSystem.getLength`（`File.length()`） |
| 3 | `Linux.lstat` | `Os.lstat` |
| 4 | `ErrnoException` 类名 | 已在 `a6f5b30` 修 |
| 5 | `open`/`access` 空参抛异常 | 已在 `d0d9e3d` 修 |
| 6 | `getLastModifiedTime0` | 已在本次（`add-ufs-3more`）补 |

**补齐这 6 项之后，才谈得上"开 JAVA_IO 不明显变差"。**

---

## 五、一句话总结

> **接手前能完成多种功能，是因为容器的主力机制是 `VirtualContext`（把 Context 派生路径改到沙箱），
> 而 Java hook 默认是关的。
> 一旦打开 hook，它就从"增强"变成"接管"——所有 Java 文件操作都走我们的包装层，
> 于是任何一点包装偏差（异常类型、返回值、缺失的方法）都会让原本正常的功能崩掉。**
>
> **这就是"不完善的 hook 比没有 hook 更危险"的原因，也是"尊重容器与系统差异"的具体含义：
> 只接管 Context 覆盖不到的部分，且接管时必须完整复刻调用方的假设。**
