# 01-Brainstorming Hooks (Think Before Coding)

## hook-01-01: Ambiguity Score (三思而后行)
- **When**: during analysis
- **Rule**: Score < 8 → block Planning entry. **不带假设进入下一阶段**。
- **Check**: goal is single+quantified, terms defined

## hook-01-02: 3 Interactive Questions (via ask_user tool)
- **When**: during option selection
- **Rule**: ≥3 questions, each with ≥2 options + rationale + risk. **主动暴露不确定性**。
- **Check**: template filled with options
- **Tool**: **必须**使用 `ask_user` 工具提问，**禁止**在正文中输出编号选项列表

## hook-01-03: Pre-mortem
- **When**: after design proposal
- **Rule**: list top-3 failure causes + prevention
- **Check**: Pre-mortem section filled

## hook-01-04: ADR for Major Decisions
- **When**: before stage exit
- **Rule**: major arch/API change → record why
- **Check**: EVOLUTION.md or project ADR

## hook-01-05: Design Approval Gate
- **When**: post
- **Rule**: explicit approval required to proceed
- **Check**: template "Gate" marked approved
