# tools/analyze —— 可复用的分析工具

这些工具不是一次性脚本，改 hook / 排查路径问题时**会反复用到**。

用法约定：在仓库根目录执行，输入文件默认放在 `dist/`（该目录不入库）。

---

## AOSP 契约对照

| 工具 | 用途 |
| --- | --- |
| `derive-contracts.py` | 从「调用方假设」反推 hook 必须满足的行为契约。改任何 Java 层 hook 前先跑它 |
| `screen-aosp-points.py` | 三源交叉（AOSP 源码 / 设备 dex / 我们的注册表），筛出还需要参考 AOSP 的点位 |
| `verify-filesystem-abstract.py` | 验证 JDK 的 `File -> FileSystem -> UnixFileSystem` 委托链，说明"为什么 native 在那个类" |
| `verify-idempotent-translate.py` | 验证路径翻译幂等（翻译后的沙箱路径不再命中任何规则）——多层 hook 并存的安全前提 |

依赖 `tools/ref/` 里的 AOSP 源码：
- `libcore_io_Linux.cpp`（`Linux.*` 的实现）
- `UnixFileSystem_md.c`（`UnixFileSystem.*0` 的实现）

**注意**：`UnixFileSystem_md.c` 是旧版源码，方法是否 native 必须用设备 dex 核对
（`getLength` / `delete` / `rename` 在设备上已经是 Java 方法）。

---

## dex / 字节码分析（给任意 apk 用）

| 工具 | 用途 |
| --- | --- |
| `dump-by-sig.py` | 按「类 + 方法名 + 签名」精确提取一个方法的完整字节码 |
| `dump-full-method.py` | 按方法名提取完整字节码（同名重载会取第一个） |
| `dump-via-class.py` | 列出某个类的方法、字符串常量、invoke 序列 |
| `list-methods.py` | 列出某个类的全部方法签名 |
| `find-via-filter.py` | 找出「含 listFiles 且随后做 isFile/length/exists 判断」的方法 = 过滤点候选 |
| `find-zero-check.py` | 提取某方法里的比较/跳转指令，用于定位"某字段为 0 就丢弃" |
| `trace-list-caller.py` | 找某个方法的调用方 |

前置：先把 apk 的 dex 解出来，再 `dexdump -d` 成 dump.txt（100MB+ 量级）。

```powershell
# 解 dex
python -c "import zipfile,os; z=zipfile.ZipFile('dist/x.apk'); [open('dist/dex/'+os.path.basename(n),'wb').write(z.read(n)) for n in z.namelist() if n.endswith('.dex')]"
# 反编译
& "$env:LOCALAPPDATA\Android\Sdk\build-tools\<ver>\dexdump.exe" -d dist/dex/classes.dex > dist/dex/dump.txt
# 分析
python tools/analyze/dump-by-sig.py "Lha/e;" "b" "(Ljava/lang/String;)Ljava/util/List;"
```

---

## 原生库 / 设备事实

| 工具 | 用途 |
| --- | --- |
| `check-jni-exports.py` | 检查 `.so` 是否导出 `Java_*` 符号 —— 决定能否用 `dlsym` 拿原实现 |
| `app-api-survey.py` | 统计多个 app 的 `File` / `Os` / NIO 用量，决定该 hook 哪一层 |
| `inspect-viadb.py` | 检查应用的 SQLite 库表结构，判断某个列表的数据来源是数据库还是文件扫描 |
| `survey-silent-failures.py` | 扫描"路径相关但无日志"的函数，找静默失败风险点 |

签名审计的主工具在 `tools/device/jni-sig-audit.py`（它需要设备连接，所以不放在这里）。
