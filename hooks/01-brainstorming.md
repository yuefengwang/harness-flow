# 01-Brainstorming Hooks (Think Before Coding)

## hook-01-01: Ambiguity Score (三思而后行)
- **When**: during analysis
- **Rule**: Score < 8 → **提示并记账，不硬拦**。**不带假设进入下一阶段**
  仍是目标，但执行它的是 agent 的持续提问，不是门禁的拒绝。
- **Check**: **由 harness 真实校验但只作参考**
  （`output_check.read_ambiguity_score` + `stage_state.record_ambiguity`）——
  在产出区里找 `歧义分数：<0-10>`，低于 8 或读不到都会在门禁输出里记 ❓
  并写进 `.state` 的 `stages.01-brainstorming.ambiguity`，但**不阻断推进**。
- **为什么不硬拦**（A13）：分数是 agent **自评**，不是 harness 观测到的
  事件。判据的输入由被判者提供时，它不写判据就读不到，门禁只能报
  「你没写 X」—— 而真正的问题往往在别处。实测三个任务（maybework /
  7090 / ppppp）收到同一句报错，根因分别是正则太窄、阶段没走完、
  提问无收敛出口。硬拦把三种病报成一种，还让用户无路可走。
  真正拦住空转的是**硬规则**：产出区实质内容 ≥ 80 字符（读 harness 落盘的
  围栏区，agent 绕不过去）。判例见 A0 的 2.9.17。
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
