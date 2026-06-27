# 01-Brainstorming Hooks (Specification-Driven)

## hook-01-01: Ambiguity Score (可执行性验证)
- **When**: during analysis
- **Rule**: Score < 8 → block Planning entry. **不带假设进入下一阶段，规约必须无歧义且可执行**。
- **Check**: goal is single+quantified, terms defined

## hook-01-02: 3 Interactive Questions (主动作业与提问)
- **When**: during option selection
- **Rule**: ≥3 questions, each with ≥2 options + rationale + risk. **主动暴露边界条件与不确定性**。
- **Check**: template filled with options
- **Tool**: **必须**使用 `ask_user` 工具提问，**禁止**在正文中输出编号选项列表

## hook-01-03: Research Context & Scope (调研上下文与范围界定)
- **When**: before design proposal
- **Rule**: 必须调研所依赖的外部包及技术约束，并划定核心意图与非目标（Non-Goals）。
- **Check**: Intent & Scope 和 Research Context 章节已填满。

## hook-01-04: Executable Scenarios (业务契约定义)
- **When**: before stage exit
- **Rule**: 必须列出至少 2 个以上具有清晰输入、预期输出及异常行为的业务 Acceptance Scenarios。
- **Check**: Executable Scenarios 章节表格已填满。

## hook-01-05: Design Approval Gate
- **When**: post
- **Rule**: 规约必须获得用户显式确认才可向下扭转。
- **Check**: template "Gate" marked approved
