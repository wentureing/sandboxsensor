# docs/changes/ —— 大量修改的落盘计划与实际 diff

本目录用于满足 `AGENTS.md` 的约束：**大量精确修改前必须先落盘计划，改完再落盘实际 diff。**

## 命名

```
<YYYYMMDD>-<任务名>.md          # 修改计划（改之前写）
<YYYYMMDD>-<任务名>.diff        # 预期/实际 diff（命令行生成）
<YYYYMMDD>-<任务名>.actual.diff # 实际 diff（改完之后）
```

## 模板

```markdown
# <任务名>

## 目标
一句话。

## 影响范围
| 文件 | 改动类型 |
| --- | --- |
| path/a.kt | 修改 |
| path/b.kt | 新增 |

## diff 预览
（把 `git diff` 的输出贴进来，或直接放 .diff 文件并在此处引用）

## 风险
- ...

## 回退方案
```powershell
git -C _ref/ShitJumpingVM revert <sha>
```

## 验证方法
```powershell
# 命令
```
判据：...

## 实际变更
（改完之后补，附 .actual.diff 路径）
```

## 已存在的记录

| 日期 | 任务 | 状态 |
| --- | --- | --- |
| — | （暂无） | — |

## 为什么放这里而不是 docs/records/

- `docs/records/` 是**结论归档**（研究、诊断、调查）
- `docs/changes/` 是**过程证据**（打算怎么改 / 实际改了什么 / 差异在哪）
- 两者混在一起会让"结论"被"过程"淹没
