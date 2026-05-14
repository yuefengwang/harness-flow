# Harness-Flow: AI-Native Workflow (Essential Flow)

基于 OpenAI 与 Gemini CLI 最佳实践构建的 AI 驱动开发工作流。核心遵循 **Research -> Strategy -> Execution** 宏观周期与 **Plan -> Act -> Reflect** 微观循环。

---

## 目录结构

| 路径 | 说明 |
|------|------|
| `STATUS.md` | 中心看板，记录当前焦点和任务状态 |
| `EVOLUTION.md` | 演化日志，记录 ADR 与流程优化 |
| `templates/` | **Essential Flow** 标准化交付物模板 |
| `hooks/` | 强制验证规则 (Guardrails)，定义不可跳过的检查点 |
| `tasks/` | 活动任务的工作目录 |
| `archive/` | 历史任务存档与会话反思记录 |
| `README.md` | **本文件** — 系统架构参考 |

---

## 阶段总览 (The Five Stages)

| # | 阶段 | 核心目标 | 交付物 |
|---|------|----------|--------|
| 01 | **Brainstorm** | 歧义消除与方案设计 | 设计 Specs / ADR |
| 02 | **Plan** | 任务 DAG 编排 | 任务 DAG / 测试策略 |
| 03 | **Coding** | 外科手术式更新 | 经验验证的代码变更 |
| 04 | **Review** | 跨组件影响与安全审计 | 经过评审的 PR / 变更 |
| 05 | **Archive** | 项目记忆与流程反思 | 更新后的 GEMINI.md / MEMORY.md |

---

## AI Agent 操作核心指令

1. **验证是唯一路径**: 所有变更必须有复现脚本或测试证明其正确性。
2. **上下文效率优先**: 最小化回合数，合并工具调用。
3. **零记忆评审**: 保证代码变更对任何新开发者都是自解释的。
4. **强制入口**: 所有任务必须通过 `/sw` CLI 启动。

---

## Hook 与模板关系

- **Hooks** (`hooks/`) — **强制规则**: 定义 "做什么" 和 "如何验证"。
- **模板** (`templates/`) — **交付结构**: 定义 "记在哪里" 和 "呈现格式"。

AI Agent 执行时必须：先读 Hook 确保合规，再按模板记录过程。
