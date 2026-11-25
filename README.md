# sandboxsensor

**主仓库**：探针 APK + 文档 + **全部 CI**。

- 容器骨架在另一个仓库：[`wentureing/ShitJumpingVM`](https://github.com/wentureing/ShitJumpingVM)（fork 自 NEXTVM）；
  本地参考副本 `_ref/ShitJumpingVM`（已 gitignore，仅作代码阅读/拆分来源）。
- 两个仓库**不共享** Gradle 配置：各自独立 `settings.gradle.kts`（AGP 8.7.3 / Gradle 8.9 / NDK 27.1.12297006 / compileSdk 35）。

## 当前状态

**看 `STATUS.md`**（唯一「当前状态」入口）；能力明细见 `docs/现状/README.md` 与
`docs/现状/容器能力清单与回归方法.md`。

## 目录

```
.
├── STATUS.md                 当前状态（唯一入口）
├── AGENTS.md                 项目约定（拆分判据 / 文档镜像规则 / CI 口径 / 新增规则 11–17）
├── docs/                     see docs/README.md
│   ├── 计划/                 要做什么、什么顺序
│   ├── 架构/                 怎么运作（改代码前必读）
│   ├── 设计/                 为什么这么选
│   ├── 现状/                 现在什么生效（能力表 + 回归清单）
│   ├── 输出/                 记录/变更/调研/调试手册
│   ├── 代码镜像/             与代码同路径的一文件一文档（规则 7）
│   └── archive/              错误结论 + 已完成计划
├── probe/                    探针 APK（com.sandbox.probe），独立 APK
├── tools/
│   ├── release/gh-api.mjs    CI 触发/取消/产物下载
│   ├── lint/                 kotlin-lint / check-imports / check-hook-coverage 等
│   ├── device/               adb 采集脚本
│   └── probe/                探针报告解析
├── _ref/                     容器仓库参考副本（gitignore）
└── .github/workflows/        probe-build / container-build / module-test
```

## 构建：全部在 GitHub Actions

| 目标 | 命令（手动 dispatch，一次一个） |
| --- | --- |
| 探针 APK | `node tools/release/gh-api.mjs --dispatch probe-build.yml main` |
| 容器 APK | `node tools/release/gh-api.mjs --dispatch container-build.yml main ref="baseline-v150"` |
| 模块单测 | `node tools/release/gh-api.mjs --dispatch module-test.yml main ref="<分支>" modules=":core:virtualization"` |

- 产物走 **Release asset**（tag `apk-latest`），命名 `<类型>-<7位sha>[-<abi>].apk`
- 容器构建：**Unit tests 已恢复且发包前置**（单测失败仍红，但产物已发布）
- `module-test`：**模块级 matrix**（virtualization / hook / sandbox 并行）
- `push` 不触发任何编译

### 固定缓存键

| 缓存 | key |
| --- | --- |
| Android SDK | `sdk-android-35-bt35.0.0-ndk27.1.12297006-cmake3.22.1-<CACHE_EPOCH>` |
| Gradle home | `gradle-home-8.9-libs-<hashFiles(libs.versions.toml)>-<CACHE_EPOCH>` |

失效：改 workflow 的 `CACHE_EPOCH`（三处保持一致）。

## 本地静态检查（提交前，毫秒级）

```powershell
node tools/lint/kotlin-lint.mjs <目录或文件>
node tools/lint/check-imports.mjs <模块 src 目录>
node tools/lint/check-test-fn-names.mjs <测试文件>   # 反引号函数名不得含 . ; [ ] / < > :
```
