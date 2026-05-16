# Hooks: Guardrails

Hooks define **mandatory quality gates** per stage. All hooks must pass before advancing to the next stage.

## Principle
1. **Unskippable**: every hook must be verifiable (command, log, file state).
2. **Empirical**: evolve hooks based on retro findings (see `workflow/STATUS.md`).

## Index

| Stage | File | Focus |
|-------|------|-------|
| 01 | `01-brainstorming.md` | Ambiguity, Pre-mortem, ADR |
| 02 | `02-planning.md` | Task DAG, Test strategy, Atomicity |
| 03 | `03-coding.md` | Surgical updates, Red-Green, Commit hygiene |
| 04 | `04-review.md` | Impact, Security, Zero-memory review |
| 05 | `05-archive.md` | Memory, Retro, Cleanup |

## Relation to Templates

- **Hooks**: what must be true
- **Templates**: what to record
