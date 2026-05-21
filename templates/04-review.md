# 04-Review

> Hooks: `hooks/04-review.md`

## Zero-Memory Review
Is the diff self-explanatory? ___

## Impact Analysis
- **Side effects**: ___
- **Regression tests**: ___

## Security
- [ ] No hardcoded secrets
- [ ] Input validated (SQL/command injection)
- [ ] Access control OK

## Review Decision
> ⚠️ **必须使用 ask_user 工具与用户交互确认 Route 决策**。不得自行填写。
> 向用户汇报审查结论（通过/不通过项、影响分析），然后请用户选择：推进归档 或 返工到指定阶段。
>
- **Route**: `___` (05-Archive / 03-Coding / 02-Planning / 01-Brainstorming)
- **Reason**: ___

### Reroute Evidence (仅在返工时填写)
| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |
|---|------|---------|---------|-------------|
| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |
| 2 | ___ | high/med/low | coding/planning/brainstorming | ___ |

## Gate
- [ ] Full build: `___`
- [ ] Lint/static analysis pass
- [ ] Doc/config in sync
