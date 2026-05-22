# 03-Coding Hooks (Simplicity & Surgical)

## hook-03-01: Surgical Updates (外科手术式改动)
- **When**: during
- **Rule**: only touch task-scoped code. No gratuitous refactoring. **只清理自己产生的垃圾**。
- **Check**: `git diff` shows only intended changes

## hook-03-02: Empirical Verification (目标驱动执行)
- **When**: during/post
- **Rule**: Red → Green required. Never assume fixes. **定义成功标准并循环验证**。
- **Check**: repro script fails before, passes after

## hook-03-03: Simplicity First (简约至上)
- **When**: during (branching/multi-state)
- **Rule**: Strategy/State patterns preferred. Max 3 nested if-else. **拒绝过度设计，50行能写完绝不写200行**。
- **Check**: code review confirms clear logic flow

## hook-03-04: Atomic Commits
- **When**: post
- **Rule**: each commit scoped to one sub-task. Conventional Commits.
- **Check**: `git log` format compliant

## hook-03-05: Doc in Sync
- **When**: during
- **Rule**: update doc/comments when logic changes
- **Check**: code change includes doc updates
