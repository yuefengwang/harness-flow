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
> ⚠️ **Route 决策**：根据分析判断直接填写 Route，无需等待用户确认。
> 可用 ask_user 时优先交互确认；不可用时自行基于分析判断填写。默认为 05-Archive（归档）。
>
- **Route**: `___` (05-Archive / 03-Coding / 02-Planning / 01-Brainstorming)
- **Reason**: ___

### Reroute Evidence (仅在返工时填写)
| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |
|---|------|---------|---------|-------------|
| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |
| 2 | ___ | high/med/low | coding/planning/brainstorming | ___ |

## Gate
<!-- 由 sw 渲染，编辑无效；签署状态存于 .state -->
- [ ] Full build: `___`
- [ ] Lint/static analysis pass
- [ ] Doc/config in sync
- [ ] README.md CLI commands verified
