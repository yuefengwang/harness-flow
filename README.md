# Simple Workflow

一套结构化、状态驱动、可自我演化的 AI 辅助开发工作流。

## 架构

```
Harness-Engineering Platform    ← 调度层：多项目/多 agent/worktree 并行
        │
        ▼
   Workflow System              ← 执行层：任务级生命周期管理
        │
        ▼
   repo/ 多项目                 ← 项目层：业务代码
```

- **Harness 平台** — 通过 git worktree 派发独立任务，每个 worktree 运行一个 AI Agent，互不干扰
- **Workflow 系统** — 每个任务经历 `头脑风暴 → 规划 → 编码 → 评审 → 归档` 五个阶段
- **repo/ 目录** — 统一管理多个项目代码，通过 `dev-init.sh` 切换开发焦点

## 快速开始

```bash
# 1. 派发一个新任务到独立 worktree (repo 项目)
./harness/dispatch.sh sample-java-app add-auth --agent claude

# 或者：开发平台自身
./harness/dispatch.sh harness-flow update-template --agent opencode --launch

# 2. 进入 worktree 启动 AI Agent
cd .worktrees/add-auth && claude .

# 3. 完成 → 提交 PR
./harness/pr.sh add-auth -m "Add authentication"

# 4. PR 合并后清理 worktree
./harness/cleanup.sh add-auth
```

## 集成到现有项目

复制工作流到你的项目：

```bash
cp -r workflow/ dev-init.sh GEMINI.md <your-project-root>/
```

详细集成说明见 `workflow/README.md`。

---

### 核心文件

| 文件 | 作用 |
|------|------|
| `harness/` | 调度层：dispatch / status / pr / cleanup |
| `workflow/` | 执行层：模板、任务、状态看板 |
| `hooks/` | 强制规则：各阶段不可跳过的检查点 |
| `docker/` | Docker 镜像构建（Agent 容器隔离） |
| `dev-init.sh` | 项目初始化 |
| `repo/` | 多项目代码 |

> 详细的工作流阶段说明、模板指南、演化规则 → 见 [`workflow/README.md`](workflow/README.md)
