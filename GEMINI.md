# AI Agent 操作指南

## ⚠️ 强制约束

**所有任务必须通过 `/sw` CLI 统一入口启动，禁止直接编写代码。**

```bash
/sw init --type=feature --name=<task-id> --session=<session>
```

绕过 `/sw` 直接编码 = 违反工作流，不被允许。

## 身份认知

你正在 **Harness-Engineering 工作流** 中运行。

### 隔离层级

你的运行环境有两层隔离：

| 层级 | 机制 | 作用 |
|------|------|------|
| 环境隔离 | Docker 容器 | 依赖、工具链、运行时独立 |
| 代码隔离 | git worktree | 独立分支，与其他任务并行开发 |

```
Docker 容器 (sw-agent)
  └── worktree: .worktrees/<task-name>  ← 挂载到 /workspace
       └── branch: harness/<project>/<task-name>
```

所有代码变更只影响当前 worktree 的分支，不影响主线和其他 worktree。如果通过 Docker 运行，你的 /workspace 即 worktree 目录。

---

## 启动方式

你有两种运行模式：

### Docker 容器模式（环境完全隔离）
```bash
harness/dispatch.sh <project> <task> --agent opencode --docker --launch
```
你的运行环境是一个独立的 Docker 容器，/workspace 即 worktree 目录。

### 直接模式（仅 worktree 隔离）
```bash
harness/dispatch.sh <project> <task> --agent claude --launch
```
或手动进入:
```bash
cd .worktrees/<task-name>
```

---

## 操作流程

### 0. 检查上下文
- 读取 `workflow/current-context.md` — 当前项目的背景信息
- 读取 `workflow/STATUS.md` — 当前任务状态
- 定位 `.harness/task.yaml` 获取任务元数据

### 1-5. 标准阶段
执行顺序不可跳过：

```
头脑风暴 (01) → 规划 (02) → 编码 (03) → 评审 (04) → 归档 (05)
```

每个阶段执行时：
1. 先读该阶段的 `hooks/0X-*.md` 了解强制规则
2. 再按 `templates/0X-*.md` 的结构执行并填写内容
3. 逐条校验 hooks 中的验证条件

### 完成归档
归档时必须按 `05-archive.md` 的要求同步 README 文档。

### 提交与清理
在 worktree 中完成工作后：
```bash
# 提交变更到当前分支
git add -A && git commit -m "[project] 功能描述"

# 推送到远程
git push origin HEAD

# 由上级调度执行 PR 创建和 worktree 清理
```
或交由 `harness/pr.sh` 和 `harness/cleanup.sh` 处理。

---

## 派发子任务

如果当前任务需要进一步拆分为多个并行子任务，可以派发新的子 Agent：

```bash
# 派发子任务到独立 worktree（直接模式）
../../harness/dispatch.sh <project> <sub-task> --agent claude --launch

# 派发子任务到 Docker 容器（环境完全隔离）
../../harness/dispatch.sh <project> <sub-task> --agent opencode --docker --launch
```

子任务在独立的 worktree + 容器中并行运行，完成后通过 PR 合并回主线。

---

## 关键文件

| 文件 | 作用 |
|------|------|
| `workflow/STATUS.md` | 状态看板，记录当前焦点 |
| `workflow/current-context.md` | 项目上下文 |
| `workflow/templates/` | 各阶段模板（结构 + 填写区） |
| `hooks/` | 各阶段强制规则（检查点 + 验证条件） |
| `.harness/task.yaml` | 任务元数据

## Dashboard（Web 控制台）

Harness 平台提供 Web 可视化控制台：
```bash
pip install -r harness/dashboard/requirements.txt
python3 harness/dashboard/server.py
# → http://localhost:8090
```

功能：查看所有活跃 Agent 状态、项目列表、任务详情，以及通过表单派发新任务。
