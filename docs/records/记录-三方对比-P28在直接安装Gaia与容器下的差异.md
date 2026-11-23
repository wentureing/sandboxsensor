# 三方对比：同一探针 v1.8 的 P28 在「直接安装 / Gaia / 我们容器」下的差异

- 日期：2026-11-23 23:00
- 探针：v1.8（`488cd35`，`versionCode=9`）
- 测试文件：`/sdcard/Download/probe-wtest.txt` = `ORIGINAL`（9 字节，`660 u0_a0 media_rw`）

---

## 一、结果（三份报告，同一份探针代码）

| 项 | **直接安装**（uid 10324） | **Gaia 内**（uid 10319） | **我们容器 p26**（uid ≈10319） |
| --- | --- | --- | --- |
| `mode` | `installed` | `container:dir-other(...gaia/data/user/0/com.sandbox.probe)` | `container:dir-other(...files/virtual/data/...)` |
| `J01` getDataDir | `/data/user/0/com.sandbox.probe` | `.../gaia/data/user/0/com.sandbox.probe` | `.../files/virtual/data/com.sandbox.probe_<ts>` |
| **`P28` fopen_r** | ❌ `errno=13` **Permission denied** | ❌ `errno=13` **Permission denied** | ✅ **`ok`** |
| **`P28` fopen_rplus** | ❌ `errno=13` | ❌ `errno=13` | ✅ **`ok`** |
| **`P28` fwrite** | —（没到写） | —（没到写） | ✅ **6 字节** |
| **真实文件内容** | **`ORIGINAL`** | **`ORIGINAL`** | **`HACKEDAL`** |
| `N03.open_literal`（`/data/data/<pkg>/files/...`） | ✅ `fd=152` | ❌ `errno=2` | ❌ `errno=2` |
| `N06.fopen_write` | ✅ `wrote 5 bytes` | ❌ `errno=2` | ❌ `errno=2` |
| `N10.maps_scan` | `lines=3102 pkg_hit=7` | `lines=2906 pkg_hit=8` | `lines≈2900 pkg_hit=8` |

---

## 二、三个关键结论

### 结论 1：**Gaia 的 P28 是 DAC 拒绝，不是路径重定向**

`errno=13 (Permission denied)` 而不是 `errno=2 (No such file)`。

- 如果是**路径重定向**，会去沙箱找 → 沙箱没有 → **`ENOENT`**
- 而 Gaia 得到的是 **`EACCES`** → 说明**它在真实路径上就是因为权限被拒**

**所以 Gaia 靠的是「guest 没有存储权限」**，不是靠路径隔离。

### 结论 2：**我们能写，是因为 guest 继承了容器的 `MANAGE_EXTERNAL_STORAGE`**

| 环境 | 容器/宿主是否申请并授权了 `MANAGE_EXTERNAL_STORAGE` | guest 的 P28 |
| --- | --- | --- |
| 直接安装 | 探针自己没申请 | `EACCES` |
| **Gaia** | **没有授权**（`appops` 未 allow） | `EACCES` |
| **我们容器** | **`allow`**（用户手动授予） | **成功写入** |

**这是本次最重要的发现**：
> **容器申请 `MANAGE_EXTERNAL_STORAGE` = 所有 guest 也获得「所有文件访问」的能力。**
> 因为 guest 是容器的子进程，共享同一个 uid/权限集合。

**这条直接推翻了"申请权限只是为了让 INSTALL 能读文件"的假设** —— 它的副作用是**放开了所有 guest 对 `/sdcard` 的写入**。

### 结论 3：**`N03`/`N06` 的失败不能作为"隔离好"的证据**

| 环境 | `N03`（`/data/data/com.sandbox.probe/files/...`） |
| --- | --- |
| 直接安装 | ✅ `fd=152`（这是它**真实的** dataDir，能写） |
| Gaia / 我们容器 | ❌ `errno=2`（这个路径在容器里不存在） |

**两义**：可能是"重定向到沙箱后不存在"，也可能是"根本没重定向、真实路径不存在"。
**只有 `P28` 是干净判据**（真实路径存在、沙箱不存在）。

---

## 三、修正之前的一个说法

`记录-P28实测推翻越权修复前提.md` 里我写：

> 真正原因：`libprobe.so` 的 GOT 没被 patch

**这个说法需要加限定**：

| 环境 | `libprobe.so` 被 patch 了吗 | 判据 |
| --- | --- | --- |
| **我们容器** | 大概率**没被 patch** | `P28` 成功写入真实路径（若被 patch，应被重定向到沙箱 → `ENOENT`） |
| **Gaia** | **无法判断** | `P28` 被 DAC 拒（`EACCES`），无论是否 patch 都拿不到可写句柄 |

**所以**：
- **"GOT 没被 patch"这个结论只在我们容器里成立**（因为只有我们能走到"写"这一步）
- **Gaia 的 `EACCES` 掩盖了它自己的 hook 覆盖问题** —— 它可能也有同样盲区，只是被 DAC 挡住了

**这意味着**：
- **我们如果关掉 `MANAGE_EXTERNAL_STORAGE`，`P28` 应该也变成 `EACCES`** —— 这是可验证的推断
- **方向 A（patch `libnativeloader`）的价值不变**，但**优先级可以调整**：先关权限验证推断，再决定要不要改 hook

---

## 四、可立即验证的推断（不需要改代码）

```
1. 把容器的 MANAGE_EXTERNAL_STORAGE 撤销（appops set ... default 或用户在设置里关）
2. 重新跑 P28
3. 预期：P28 从「ok + fwrite=6」变成「errno=13 Permission denied」（与 Gaia 一致）
4. 恢复 ORIGINAL 后再试 INSTALL /sdcard/Download → 预期失败（回到最初的 APK file not found）
```

**如果推断成立**：
- 「我们能写 `/sdcard`」= **权限副作用**，不是 hook 缺陷
- 「GOT 没被 patch」仍然存在，但**它的可观测后果需要另一个判据**（不能用 `P28`）

**这时需要新的判据**：在**沙箱内**放一个文件，测 guest 是否能通过**真实路径**读到它（若有重定向，真实路径读不到，沙箱路径能读到）。

---

## 五、给后续的结论

| 结论 | 证据 |
| --- | --- |
| **容器的 `MANAGE_EXTERNAL_STORAGE` 会给所有 guest 开 `/sdcard` 写权限** | 三方对比（只有我们 allow） |
| Gaia 的 `/sdcard` 保护靠 **DAC**，不是路径隔离 | `EACCES` 而非 `ENOENT` |
| `P28` 在我们容器里能写，**至少有两个原因叠加**：权限继承 + hook 未覆盖 | 需要撤销权限后复测才能分离 |
| **`N03`/`N06` 不是有效判据** | 两种原因都返回 `ENOENT` |
