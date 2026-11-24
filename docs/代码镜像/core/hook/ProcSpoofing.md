# 代码镜像：`core/hook/ProcSpoofing.kt`

> v154 从 `NativeHookBridge.kt` 拆出。

## 职责

**做什么**
- `spoofProcSelf(pid, pkg)`：注册 `/proc/self/{cmdline,comm,status}` 的伪造内容
- `setProcSpoofSuspended(b)`：临时关闭 `/proc` 伪造
- `setProcessName(name)`：`prctl` 改内核 comm
- `armSeccompDiag(path)`：启用 raw-syscall 诊断（只记录、不改结果）
- `spoofSystemProperty` / `spoofSystemProperties` / `propertyOverridesSnapshot`
- `buildFakeProcStatus(pid, pkg)`：生成伪造的 `/proc/self/status`（**纯函数**）

**不负责**
- ❌ 伪造内容的**存储**（通过 `setFakeContent` 回调写回 `NativeHookBridge.fakeFileContent`）
- ❌ native 实现（全部回调注入，未就绪时空操作）
- ❌ 路径规则 / 隐藏路径（`PathRedirectRegistry` / `NativeHookBridge`）

## 偏差登记

| # | 偏差 | 类型 | 说明 |
| --- | --- | --- | --- |
| 1 | `/proc/self/*` 返回伪造内容 | ✅ **必要** | 否则 guest 直接看到容器包名/进程名；容器隐匿的基础 |
| 2 | 可临时关闭伪造（`setProcSpoofSuspended`） | ✅ **必要** | 实测：CM 客户的 SecNeo mini-linker 构造时解析**真实** maps 来解析 libc 符号，伪造 maps 会让注册链静默失败 |
| 3 | 属性覆盖只对 `__system_property_get` 生效 | ⚠️ **能力边界** | 直接读 `/dev/__properties__` 或 `__system_property_foreach` 的代码看不到覆盖 |
| 4 | `spoofedPid` 只影响伪造内容 | ✅ | 真实 pid 不变（Binder 里仍是真实值） |

## 约束与坑

1. **必须 guest 进程调用**（`/proc/self` 是进程级）。
2. `comm` 有 **15 字符上限**（Linux 内核限制）→ 代码里 `take(15)`。
3. 伪造内容写进 `fakeFileContent` 后，**读取路径必须已被 hook** 才生效；直接 `syscall` 读仍拿真实内容（偏差 #3）。
4. native 未就绪时只更新 Kotlin 侧状态，不报错（保证 bind 早期可调用）。
5. 单测：`core/hook/src/test/.../ProcSpoofingTest.kt`（5 例，纯 JVM：伪造内容含包名/pid、不含容器特征、写入三份、comm 截断、reset）。

## 相关

- `docs/代码镜像/core/hook/PathRedirectRegistry.md`
- `docs/架构/架构-检测面与优先级.md`
