# 修改计划：编译 CI 改为「只能显式 dispatch，一次一个」

- 日期：2026-11-24
- 触发原因：用户要求「**要触发编译的 CI 只能触发一次编译，明确指定是容器或者探针编译**」
- 当前状态：**不满足**，本文是修正方案

---

## 一、目标

一句话：任何一次编译都必须由人**明确指定**跑哪一个（容器 or 探针），且一次只跑一个。

---

## 二、现状为什么不满足

| workflow | 当前触发 | 问题 |
| --- | --- | --- |
| `probe-build.yml` | `push`（paths: `probe/**`、`gradle/**`、`settings.gradle.kts`、`build.gradle.kts`、**自己**）+ `workflow_dispatch` | 改根构建文件或改 workflow 自己都会自动编译探针 |
| `container-build.yml` | `push`（paths: **只有自己**）+ `workflow_dispatch` | 只要提交里含 `container-build.yml`，容器就自动编译一次 |

**根本问题**：`push` 的 `paths` 是**按「变更集里有没有这些文件」**判断的。
一次 push 如果同时改了 `probe/**` 和 `.github/workflows/container-build.yml`，**两个 workflow 都会跑** ——
这不是配置写错，而是「自动触发」这个机制本身无法保证「只跑一个」。

2026-11-24 的真实事故就是它：

```
# 我一次 push（41c5cb1 + 2852d50）同时含：workflows 文件 + probe 源码
45386771719  build-probe      push  in_progress   2852d50   ← 预期
45386771274  build-container  push  in_progress   2852d50   ← 不该跑
```

结论：**要满足「明确指定」，必须取消自动触发，只保留 `workflow_dispatch`。**

---

## 三、影响范围

| 文件 | 改动类型 | 说明 |
| --- | --- | --- |
| `.github/workflows/probe-build.yml` | 修改 | 删除 `on.push` 整段（含 paths） |
| `.github/workflows/container-build.yml` | 修改 | 删除 `on.push` 整段（含 paths） |
| `AGENTS.md` | 修改 | 「CI 触发」一节改为「编译只能 dispatch，一次一个，明确指定」 |

不改任何构建步骤、缓存 key、签名逻辑。

---

## 四、逐文件 diff 预览

### 4.1 `probe-build.yml`

```diff
 on:
   workflow_dispatch:
     inputs:
       abi:
         description: '目标 ABI'
         type: choice
         options: [arm64-v8a, x86_64, universal]
         default: arm64-v8a
-  push:
-    branches: [main]
-    # 探针与容器必须分开触发（用户明确要求「一次一个、分别命令」）。
-    paths:
-      - 'probe/**'
-      - 'gradle/**'
-      - 'settings.gradle.kts'
-      - 'build.gradle.kts'
-      - '.github/workflows/probe-build.yml'
```

### 4.2 `container-build.yml`

```diff
 on:
   workflow_dispatch:
     inputs:
       repo:
         description: '容器源码仓库'
         default: 'wentureing/ShitJumpingVM'
       ref:
         description: '分支 / tag / SHA'
         default: 'main'
       task:
         description: 'Gradle 任务'
         default: ':app:assembleDebug'
-  push:
-    branches: [main]
-    # 探针与容器必须分开触发（用户明确要求「一次一个、分别命令」）。
-    # 本 workflow 只在自己文件变化时自动触发；容器源码改动后手动 dispatch 一次。
-    paths:
-      - '.github/workflows/container-build.yml'
```

### 4.3 `AGENTS.md`（CI 一节）

```diff
-## CI 触发：探针与容器**分开**，一次一个，分别命令
-
-**规则（2026-11-24 定，我理解错过一次，这里是更正版）**：
-
-> **探针与容器是两个独立的 workflow，必须分开触发，各自一次。**
-> **不要指望一次 push 触发两个，也不要一次 dispatch 两个。**
->
-> 用户的原话：「探针和容器要分开，不要一起 ci。一次一个，分别命令。」
-
-### 正确的触发方式
-
-| 场景 | 怎么做 |
-| --- | --- |
-| **改了探针**（`probe/**` 等） | push 主仓库 → `build-probe` 自动触发一次（`paths` 过滤保证只它跑） |
-| **改了容器源码**（`wentureing/ShitJumpingVM`） | 主仓库感知不到 → **手动 dispatch 一次**：`node tools/release/gh-api.mjs --dispatch container-build.yml main` |
-| **改了文档/工具** | 两个都不该跑（`paths` 会过滤掉） |
-
-**关键**：**一次只 dispatch 一个 workflow，且只调一次。**
+## CI 触发：**只能手动 dispatch，一次一个，明确指定**
+
+**规则（2026-11-24 定；push 自动触发已全部移除）**：
+
+> **两个编译 workflow 都只有 `workflow_dispatch`，没有 `push`。**
+> **要编译，必须明确指定是 `probe-build.yml` 还是 `container-build.yml`，一次一条命令。**
+>
+> 用户的原话：「要触发编译的 CI 只能触发一次编译，明确指定是容器或者探针编译。」
+
+### 为什么去掉自动触发
+
+`push` 的 `paths` 是按「变更集里有没有这些文件」判断的：
+一次 push 同时改了 `probe/**` 和 `.github/workflows/container-build.yml`，
+**两个 workflow 都会跑** —— 这不是配置错，是自动触发本身保证不了「只跑一个」。
+2026-11-24 实际发生过（见 `docs/changes/20261124-ci-manual-dispatch-only.md`）。
+
+### 正确的触发方式
+
+| 场景 | 命令（一次一条） |
+| --- | --- |
+| **改了探针** | `node tools/release/gh-api.mjs --dispatch probe-build.yml main` |
+| **改了容器源码** | `node tools/release/gh-api.mjs --dispatch container-build.yml main` |
+| **改了文档/工具** | 什么都不跑 |
+
+**push 不再触发任何编译** —— 所以 push 文档、push workflow 文件都是安全的。
+
+### 违反判据（自查）
+
+同一个 `head_sha` 出现两个 run，或同一个 workflow 出现两个 run → 就是违反。
+触发后**不要**为了「保险」再 dispatch 第二次。
```

---

## 五、风险

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | **忘记 dispatch → 产物不更新**，设备上装到旧包 | AGENTS.md 写明「改完探针必须 dispatch 一次」；本文件记录流程 |
| 2 | 有人以为 push 会自动构建，等半天没结果 | workflow 顶部注释 + AGENTS.md |
| 3 | 以后想恢复自动触发 | `git revert <sha>` |

**回退方案**：

```powershell
git revert <ci-manual-dispatch-sha>
```

---

## 六、验证方法（按顺序，可执行）

```powershell
# 1) 记录当前 run 数
node tools/release/gh-api.mjs "/repos/wentureing/sandboxsensor/actions/runs?per_page=5"

# 2) push 这次改动的提交（只含 workflow + AGENTS.md）
#    预期：run 数量【不变】—— 一个都不触发
node tools/release/gh-api.mjs "/repos/wentureing/sandboxsensor/actions/runs?per_page=5"

# 3) 明确 dispatch 探针一次
node tools/release/gh-api.mjs --dispatch probe-build.yml main
#    预期：+1 个 run，且 name=build-probe、event=workflow_dispatch

# 4) 再查一次，确认没有 build-container 的 run 出现
```

**判据**：第 2 步 run 数不变；第 3 步只 +1 且是 probe；全程无 container run。

---

## 七、执行顺序

| 顺 | 动作 |
| --- | --- |
| 1 | 改两个 workflow + AGENTS.md（本文档即计划） |
| 2 | 按第六节验证：先 push（应 0 触发），再 dispatch 探针一次 |
| 3 | 在同一文件追加 `## 实际变更` |
