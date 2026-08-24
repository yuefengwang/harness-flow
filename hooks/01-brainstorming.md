# 01-Brainstorming Hooks (Think Before Coding)

## hook-01-01: Ambiguity Score (三思而后行)
- **When**: during analysis
- **Rule**: Score < 8 → block Planning entry. **不带假设进入下一阶段**。
- **Check**: **由 harness 真实校验**（`output_check.read_ambiguity_score`）——
  在产出区里找 `歧义分数：<0-10>`，低于 8 或**读不到**都会拦下本阶段。
  从前这条规则只写在文档里、无人读取（「判据存在、无人调用」的第七例）。
- **收敛条件**：这个分数**就是**停止提问的判据 —— 不是「问够三个」。
  低于 8 就继续用 `question` 澄清；达到 8 就回填阶段文件并结束。
  **不得**在分数不够时直接把数字改大：那只会让下一阶段带着未澄清的假设开工。

## hook-01-02: 3 Interactive Questions (via question tool)
- **When**: during option selection
- **Rule**: ≥3 questions, each with ≥2 options + rationale + risk. **主动暴露不确定性**。
  这是**下界**，上界由 hook-01-01 的分数给出 —— 两者缺一，提问就没有终点
  （任务 `ppppp`：14 轮 question、零产出）。
- **Check**: template filled with options
- **Tool**: **必须**使用 `question` 工具提问，**禁止**在正文中输出编号选项列表

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
