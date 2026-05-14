# 03-Coding Hooks

## hook-03-01: Surgical Updates
- **When**: during
- **Rule**: only touch task-scoped code. No gratuitous refactoring.
- **Check**: `git diff` shows only intended changes

## hook-03-02: Empirical Verification
- **When**: during/post
- **Rule**: Red → Green required. Never assume fixes.
- **Check**: repro script fails before, passes after

## hook-03-03: Explicit Patterns for Complexity
- **When**: during (branching/multi-state)
- **Rule**: Strategy/State patterns preferred. Max 3 nested if-else.
- **Check**: code review confirms clear logic flow

## hook-03-04: Atomic Commits
- **When**: post
- **Rule**: each commit scoped to one sub-task. Conventional Commits.
- **Check**: `git log` format compliant

## hook-03-05: Doc in Sync
- **When**: during
- **Rule**: update doc/comments when logic changes
- **Check**: code change includes doc updates
