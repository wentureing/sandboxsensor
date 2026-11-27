# 变更：清理 module-test 警告中确认的 2 个缺陷 + 2 处噪音（2026-11-27）

- 触发：读 `module-test`（run `45964759538`，第 2 步那批）的全部 warning，逐类判定「缺陷 / 噪音 / 技术债」。
- 结论：**6 类警告里只有 2 处是真缺陷、2 处是可消噪音**；其余是弃用 API 与反射场景的固有权衡，不在本批动。

---

## 一、警告分类与判定

| 类 | 数量 | 是否缺陷 | 处置 |
| --- | --- | --- | --- |
| **A. Kotlin `Condition is always 'true'/'false'`** | 6 处 | **2 处是死代码（缺陷）**，4 处是防御性冗余 | 删死代码 2 行；其余不动 |
| **B. `core:common 跑过但没有 checks-template.txt`** | 1 处 | **CI 守卫误报（缺陷）** | 修 YAML 条件 |
| **C. C++ `-Wunused-function`** | 2 处 | 否（测试文件里预留的辅助函数） | 加 `[[maybe_unused]]` 消警告 |
| D. `'xxx' is deprecated`（`TYPE_TOAST`/`scaledDensity`/`Icons.Filled.*`/`get(String)` 等） | ~40 条 | 否（技术债） | **本批不动**，登记 |
| E. `Unchecked cast`（`Array<out Any>` → `Array<Any?>`、`Any?` → `ArrayMap`） | ~6 处 | 否（反射场景固有权衡） | 不动 |
| F. Node `[DEP0040] punycode` / `[DEP0169] url.parse` | 多条 | 否（GitHub Action 内部 node 依赖） | 不动 |

---

## 二、修的 4 处（全部零行为变化）

### 1) `VirtualContentProviderManager.checkReadPermission` —— 死判断

```diff
         // Same package always allowed
         if (record.packageName == callingPackage) return true
-        // Exported providers with no read permission are accessible
-        if (record.exported && record.readPermission == null) return true
         // TODO: Check against VirtualPermissionManager
         return record.exported
```

第 383 行 `if (record.readPermission == null) return true` 已经覆盖了"无 readPermission"的所有情况，
所以第 389 行 `record.exported && record.readPermission == null` **恒为 false**（编译器已证明）。删掉不改行为。

> 顺带记录：这个函数真正的语义是「无 readPermission → allow；同包 → allow；否则看 exported」，
> 而 `TODO: Check against VirtualPermissionManager` 说明**跨包权限校验还没实现**（已知未完成，
> 属 `STATUS.md` §2 那类缺口，本轮不动）。

### 2) `checkWritePermission` —— 同一形态的死判断

```diff
         if (record.writePermission == null) return true
         if (record.packageName == callingPackage) return true
-        if (record.exported && record.writePermission == null) return true
         return record.exported
```

### 3) `.github/workflows/module-test.yml` —— 守卫误报

`Show diagnostics sequence` 用 `if: always() && matrix.module == ':core:common'`，
但 `Compile` / `Run module unit tests` / `Assert tests actually ran` 三个步骤都带
`inputs.modules == '' || contains(inputs.modules, matrix.module)`。
于是**显式指定 `modules=":core:hook"` 时**，`:core:common` 本就不跑测试，
这一步却仍然执行并打 `::warning::core:common 跑过但没有 checks-template.txt` —— 纯误报（本次日志里就是它）。

```diff
       - name: Show diagnostics sequence
-        if: always() && matrix.module == ':core:common'
+        if: always() && matrix.module == ':core:common' && (inputs.modules == '' || contains(inputs.modules, matrix.module))
```

### 4) C++ 测试的两个未使用函数

`path_rules_test.cpp::splitSpaces`、`path_pattern_test.cpp::expect_eq` 是预留辅助函数，
`-Wall -Wextra` 下报 `-Wunused-function`。加 `[[maybe_unused]]`（不删，保留将来用）。

---

## 三、判据

| 层 | 判据 |
| --- | --- |
| `module-test (corehook)` / `(coreservices)` | 绿；`Assert tests actually ran` 的 `tests > 0` |
| `module-test (corecommon)` | 绿，且**不再出现** `::warning::…checks-template.txt` |
| `cpp-core-test` | 两个 `-Wunused-function` 警告消失；四个测试仍全过 |
| 行为 | `checkReadPermission` / `checkWritePermission` 返回值对同一输入不变（恒 false 条件删除） |

---

## 四、回退

```powershell
git -C _ref/ShitJumpingVM revert <本批容器 commit>
git revert <本批主仓库 commit>
```

---

## 五、实际变更

（改完补 `git diff --stat`）
