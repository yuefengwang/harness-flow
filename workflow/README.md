# Simple Workflow 系统

工作流引擎，管理任务的完整生命周期。

---

## 目录结构

| 路径 | 说明 |
|------|------|
| `STATUS.md` | 中心看板，记录当前焦点和所有任务状态 |
| `EVOLUTION.md` | 演化日志，记录重复出现的流程痛点 |
| `templates/` | 五大阶段模板，提供结构和填写指引 |
| `hooks/` | 强制规则（与模板分离），定义不可跳过的检查点 |
| `tasks/` | 活动任务的工作目录（按任务 ID 隔离） |
| `archive/` | 已完成任务的历史存档 |
| `current-context.md` | 当前项目的上下文快照（由 `dev-init.sh` 自动生成） |
| `.current-project` | 当前选中的项目标记（自动生成） |
| `README.md` | **本文件** — 工作流引擎内部参考 |

---

## 阶段总览

| # | 阶段 | 模板 | Hooks |
|---|------|------|-------|
| 01 | 头脑风暴 | `templates/01-brainstorming.md` | `hooks/01-brainstorming.md` |
| 02 | 规划 | `templates/02-planning.md` | `hooks/02-planning.md` |
| 03 | 编码 | `templates/03-coding.md` | `hooks/03-coding.md` |
| 04 | 评审 | `templates/04-review.md` | `hooks/04-review.md` |
| 05 | 归档 | `templates/05-archive.md` | `hooks/05-archive.md` |

- **模板** — 描述性指引，提供结构、上下文、填写区
- **Hooks** — 强制规则，定义不可跳过的检查点和验证条件

---

## AI Agent 操作指引

### 0. 任务启动 (所有任务必须通过此入口)

```bash
# 创建新任务（唯一入口，禁止绕过）
/sw init --type=feature --name=<task-id> --session=<session-id>

# 恢复崩溃的任务
/sw resume --name=<task-id>

# 阶段推进
/sw advance --name=<task-id>
```

`/sw init` 自动完成：创建任务目录、复制模板、初始化持久化状态、更新 STATUS.md。

### 1-4. 标准操作

1. **进入任务**：读取 `STATUS.md` 获取当前焦点，读取 `current-context.md` 获取项目背景
2. **执行阶段**：先读该阶段的 `hooks/0X-*.md` 了解强制规则，再按 `templates/0X-*.md` 的结构执行并填写
3. **完成归档**：逐条校验 `hooks/05-archive.md` 中的 README 同步规则，将任务目录移至 `archive/history/`
4. **切换项目**：根目录执行 `./dev-init.sh <project>` 更新上下文

## Hook 修改须知

修改任意 hook 后，必须同步更新「阶段总览」表格和对应的模板引用。这是 archive 阶段的 README 同步规则的一部分。
