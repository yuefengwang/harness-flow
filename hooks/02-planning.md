# 02-Planning Hooks (Implementation Plan Validation)

## hook-02-01: Task DAG (任务分解与原子性)
- **When**: during decomposition
- **Rule**: DAG with clear deps, each task 2-15 min atomic
- **Check**: each task has Do + Verify + Deps

## hook-02-02: Scenarios to Tests (用例契约转化)
- **When**: pre-coding
- **Rule**: 必须为 01 阶段定义的所有 Executable Scenarios 指定测试文件路径和用例映射计划。
- **Check**: Scenario to Test Mapping 章节已填满，且 Task 1 必须是编写测试契约与 Stub。

## hook-02-03: Interface Design Freeze (接口冻结)
- **When**: during
- **Rule**: 类、函数原型必须在 Plan 中设计清晰并冻结，严禁在 Coding 阶段随意改动接口签名。
- **Check**: Interface Design & Code Mapping 章节已有完整占位或定义。

## hook-02-04: Atomicity
- **When**: post
- **Rule**: each sub-task = single verifiable unit
- **Check**: Do + Verify present in every task

## hook-02-05: User Interaction via Tool
- **When**: 需要用户确认决策时
- **Rule**: **必须**使用 `ask_user` / `question` 工具，**禁止**在正文中输出编号选项
