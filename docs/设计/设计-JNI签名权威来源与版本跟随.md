# 设计：JNI 签名的权威来源与版本跟随机制

- 日期：2026-11-25
- 触发：用户问"验证你的这些信息、权威资料是什么、之后怎么跟随版本变化"
- 背景：本项目在 Java 层 hook 上**连续多轮**因为签名/类名写错而失败，全部发生在
  "照文档或记忆写签名"这条路上

---

## 一、权威性分级（这是本文最重要的部分）

| 级别 | 来源 | 权威性 | 理由 |
| --- | --- | --- | --- |
| **L1** | **设备上的 `core-*.jar` 里的 dex**（`/apex/com.android.art/javalib/`） | **唯一权威** | **ART 运行时加载的就是它**。`dexdump` 读出的方法表与 ART 解析同源 |
| L2 | AOSP 源码（[googlesource](https://android.googlesource.com/platform/libcore/)、[androidxref](http://androidxref.com/)） | 参考 | 是**某个分支的源码**，不等于设备产物；厂商会改 libcore；APEX 模块化让版本更难对齐 |
| L3 | 技术博客 / StackOverflow / DeepWiki | 只作**线索** | 常见"抄错版本"；本项目已被这类资料误导过多次 |
| L4 | 记忆 / 推断 | **不可作为依据** | 本轮 `opendir` 就是这么错的 |

### 关键判据

> **能在设备上跑起来的方法表，才是真的方法表。**
> 签名写错时编译器不会报（JNI 里签名只是字符串），只在运行时 `RegisterNatives` 失败，
> 而且批量注册是**全或无**，错误信息只有 `code=-1`。

### 反问一次：为什么 AOSP 源码不够

| 问题 | 例子 |
| --- | --- |
| 分支差异 | `androidxref` 默认展示的常是老版本（9.0/7.0），方法集与 Android 15 不同 |
| 厂商修改 | vivo/OPPO 等会改 libcore（本项目设备是 vivo V2463A） |
| 模块化 | Android 10+ `java.*` 移到 `core-oj.jar`、`libcore.*` 留在 `core-libart.jar`；**同一个类可能不在你以为的那个 jar 里** |
| ClassLoader 差异 | `FindClass` 与 `Class.forName` 走的加载器不同，能拿到的东西也不同 |

**本轮实证**：
- `libcore.io.Linux` **没有** `opendir`（androidxref 上某些版本有，容易误信）
- `java.io.UnixFileSystem` **不在** `core-libart.jar`，在 `core-oj.jar`
- `File.listFiles()` 的真实入口是 `UnixFileSystem.list0`，**不经过** `libcore.io.Linux`

---

## 二、验证方法：`tools/device/jni-sig-audit.py`

### 2.1 做什么

1. 从设备 `adb exec-out cat` 拉 `/apex/com.android.art/javalib/core-{libart,oj}.jar`
2. 解出 dex，用**本机 build-tools 的 `dexdump -d`** 解析
3. 提取目标类的**全部方法**（name / 签名 / 是否 native）
4. 从 C++ 源码里正则抓出所有 `{const_cast<char*>("name"), const_cast<char*>("sig"), ...}`
5. **逐条比对**，输出 `OK` / `MISMATCH`（MISMATCH 会给出"设备上真实的签名"或"该类没有此方法"）

### 2.2 用法

```powershell
python tools/device/jni-sig-audit.py `
  --src _ref/ShitJumpingVM/core/hook/src/main/cpp/native-hook.cpp

# 指定额外的类（默认扫 libcore/io/Linux、java/io/UnixFileSystem、sun/nio/fs/UnixNativeDispatcher）
python tools/device/jni-sig-audit.py --class java/lang/Runtime
```

**退出码 0 = 全部匹配；1 = 有差异** → 可以直接当 CI 卡口。

### 2.3 本轮验证结果

```
OK  open                    (Ljava/lang/String;II)Ljava/io/FileDescriptor;
OK  access                  (Ljava/lang/String;I)Z
OK  mkdir                   (Ljava/lang/String;I)V
OK  unlink                  (Ljava/lang/String;)V
OK  rename                  (Ljava/lang/String;Ljava/lang/String;)V
OK  list0                   (Ljava/io/File;)[Ljava/lang/String;
OK  createDirectory0        (Ljava/io/File;)Z
OK  createFileExclusively0  (Ljava/lang/String;)Z
```

（`nativeLoad` 那条 MISMATCH 是误报：它注册在 `java.lang.Runtime`，不在默认扫描的 3 个类里。）

---

## 三、跟随版本变化的机制（**这是可持续性的关键**）

### 3.1 三条触发条件

| 触发 | 动作 |
| --- | --- |
| **换测试设备 / 升级 Android** | 跑一次审计，把输出存成基线快照 |
| **新增任何 Java 层 hook** | 先跑审计确认签名，再写 C++ |
| **改动了 hook 注册表** | 跑审计（CI 或本地） |

### 3.2 基线快照（怎么做 diff）

把审计输出落到 `docs/现状/jni-sig-baseline/<android版本>-<设备型号>.txt`：

```powershell
python tools/device/jni-sig-audit.py > docs/现状/jni-sig-baseline/android15-vivoV2463A.txt
```

以后换版本时 `diff` 两个快照 → **哪些 native 方法被增删改了**一目了然。

### 3.3 版本适配的判断顺序

```
1. 审计发现 MISMATCH
     -> 如果"同名不同签名"：按设备签名改 C++（这是最常见的）
     -> 如果"该类没有此方法"：说明该版本换了入口类 -> 去 core-oj.jar / core-libart.jar 里找新家
2. 审计发现"我注册的方法在设备上不存在"
     -> 该版本可能把功能移到了别的类（如 Linux.opendir -> UnixFileSystem.list0）
     -> 用 find-dirent 思路在全部 jar 里按名字扫
3. 全部匹配但仍不生效
     -> 不是签名问题：查 RegisterNatives 是否成功、方法是否被 JIT 内联、ClassLoader 是否正确
```

### 3.4 不要做的事

| ❌ | 原因 |
| --- | --- |
| 照 AOSP 源码写签名就算完 | 设备可能是别的分支/被厂商改过 |
| 相信博客里的签名 | 版本不明，本项目已被误导 |
| 用 `FindClass` 找 `java.io.UnixFileSystem` 这类包私有类 | 可能返回 null；要用 `Class.forName` 走 boot ClassLoader（已封装成 `find_class_via_forname`） |
| 批量 `RegisterNatives` | 全或无，错一个就全失败且看不出是哪个；已改成逐个注册 + `ExceptionDescribe` |

---

## 四、本轮发现的完整方法清单（供后续参考）

### 4.1 `libcore.io.Linux`（`core-libart.jar`，150 方法 / 134 native）

与文件相关且**存在**的：

| 方法 | 签名 |
| --- | --- |
| `open` | `(String,int,int)FileDescriptor` |
| `access` | `(String,int)boolean` |
| `mkdir` | `(String,int)void` |
| `unlink` | `(String)void` |
| `rename` | `(String,String)void` |
| `stat` | `(String)StructStat` |
| `lstat` | `(String)StructStat` |
| `readlink` | `(String)String` |
| `fstat` | `(FileDescriptor)StructStat` |

**不存在**：`opendir`、`readdir`、`closedir`、`getdents`

### 4.2 `java.io.UnixFileSystem`（`core-oj.jar`，包私有）

| 方法 | 类型 | 对应 |
| --- | --- | --- |
| `list0(File)[String` | **NATIVE** | `File.listFiles()` |
| `createDirectory0(File)boolean` | **NATIVE** | `File.mkdir/mkdirs` |
| `createFileExclusively0(String)boolean` | **NATIVE** | `File.createNewFile` |
| `delete` / `rename` / `list` / `listRoots` | Java | 内部再调上面的 native |

### 4.3 `sun.nio.fs.UnixNativeDispatcher`（NIO2，`core-oj.jar`）

| 方法 | 类型 |
| --- | --- |
| `opendir(UnixPath)long` | **Java**（内部调 openat） |
| `readdir(long)[byte` | **NATIVE** |
| `closedir(long)void` | **NATIVE** |
| `fdopendir(int)long` | **NATIVE** |

**结论**：`File` 走 4.2，NIO2 走 4.3，**两条路都不要往 `libcore.io.Linux` 上找 `opendir`**。

---

## 五、一句话

> **权威资料是设备上的 dex，不是文档。**
> 跟随版本的方式是：**换环境就跑 `jni-sig-audit.py`，把输出存成基线快照，用 diff 看变化**。
> 这套机制的价值不在"这次修对了"，而在"下次换 Android 版本时能 30 秒内定位到哪个签名变了"。
