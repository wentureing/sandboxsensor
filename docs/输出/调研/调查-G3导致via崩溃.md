# 调查：G3 白名单导致 via 崩溃（2026-11-28，含 A/B 证据）

## 一、症状

用户在 17:35 左右尝试启动 via 两次，均失败。设备日志显示：

```
E slot No available stub for process slot 0 (instance=mark.via_1795774018778 mode=standard)
S slot reclaimed stale leases=1 stubs=[0]
```

## 二、时间线（三次尝试，全部崩在同一处）

| 时间 | 事件 |
| --- | --- |
| 17:36:16.9 | via 启动：`instr callActivityOnCreate exit ok` → `cb onActivityStarted` → **`cb onActivityResumed`（+359 ms）** → `v131-autofill total=14` |
| 17:36:16.99 | **FATAL**：`java.lang.RuntimeException: Unable to resume activity {mark.via/mark.via.Shell}: java.lang.IllegalArgumentException: fontDirectory == null || fontDirectory.isEmpty()` @ `s9.d.<init>` ← `c8.s6.a9` ← `androidx.fragment.app.q.onPostResume` |
| 17:36 | 进程 5211 退出（`VM exiting with result code 0, cleanup skipped`）⇒ slot 租约未归还 |
| 17:37:23 / 24 / 26 | 又三次 FATAL（pid 7700 / 7857 / 8135），同一异常、同一位置 |

**所以 `No available stub` 是崩溃的次生现象**（进程死 → 租约 stale → 池空），不是独立缺陷；
且容器已能自动回收：`S slot reclaimed stale leases=1 stubs=[0]`。

## 三、A/B 定因果

| 容器 | 版本 | `hook stats`（via 进程） | via 结果 |
| --- | --- | --- | --- |
| **新（含 G3 生效）** | `1.0.0-f1c29ad` | **`applied=1 slots=2`** | ❌ 每次都崩 `fontDirectory` |
| **旧（G3 未命中）** | `1.0.0-0f3b102` | **`applied=0 slots=0`** | ✅ `mark.via` 进程存活，无新 FATAL |

两者差异只有"白名单是否命中"⇒ **`libjavacore.so` 的 `open`/`openat` 被 GOT patch 后，破坏 via 的字体目录初始化**。

## 四、已知的机制线索（尚未定论）

- `IllegalArgumentException: fontDirectory == null || fontDirectory.isEmpty()` 来自 via 的混淆类 `s9.d`，
  由 `Fragment.onPostResume` 链触发（`c8.s6.a9 → s8.g.* → pa.p.w → pa.g.get`）。
- `fontDirectory` 在 Android 上通常由 `Typeface`/`FontManager` 或 `System.getProperty("java.home")` 派生；
  via 侧读到的值是**空**。
- **待查**：`hooked_open` 对"无规则命中"的路径是否与原 `open` **完全等价**（重点：`O_CREAT` 分支的 `va_arg` 取 mode、
  `O_TMPFILE`、`errno` 保真、以及 `is_path_hidden` 是否误命中字体路径）。
- 也可能是 `libjavacore` 里的调用**依赖 PLT 槽的原始值**（如 glibc/bionic 的 `__open_2`/fortify 变体），
  被替换后行为漂移。

## 五、处置（已执行）

1. **`LibAllowDefaults` 默认全空**（`PKG_ALLOW`/`LIB_ALLOW` 空）⇒ G3 代码保留、默认不启用 ⇒ **零行为变化**；
2. 重新编译容器（`146d944`），确认 via 恢复；
3. `H2.lib_allow_applied` 在期望为空时返回 `SKIPPED`，不会误报。

## 六、后续调查计划（不阻塞主线）

1. 用 `wv_read_trace` 类似的手段，记录 `hooked_open` 在**字体路径**上的实际行为（翻译前后、flags、fd、errno）；
2. 对比 `open` 与 `openat`（via 可能走 `openat`，且带 `AT_FDCWD` 之外的 dirfd）；
3. 若确认是"PLT 槽被替换导致 fortify/`__open_2` 变体漂移"，则 G3 应把 `libjavacore` 从白名单排除，
   改找**真正可安全替换**的库（例如只替换 `stat`/`access` 这类幂等查询）。
