# 02-Planning Hooks

## hook-02-01: Task DAG
- **When**: during decomposition
- **Rule**: DAG with clear deps, each task 2-15 min atomic
- **Check**: each task has Do + Verify + Deps

## hook-02-02: Test Strategy First
- **When**: pre-coding
- **Rule**: define validation method before coding
- **Check**: test strategy section filled; bugfix → repro script as Task 1

## hook-02-03: Resource Ready
- **When**: during
- **Rule**: all deps (libs, APIs, docs) confirmed available
- **Check**: no blocking external dependencies

## hook-02-04: Atomicity
- **When**: post
- **Rule**: each sub-task = single verifiable unit
- **Check**: Do + Verify present in every task

## hook-02-05: User Interaction via Tool
- **When**: 需要用户确认决策时
- **Rule**: **必须**使用 `ask_user` / `question` 工具，**禁止**在正文中输出编号选项
