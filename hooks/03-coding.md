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

## hook-03-06: User Interaction via Tool
- **When**: 需要用户确认、选择或输入时
- **Rule**: **必须**使用 `ask_user` / `question` 工具，**禁止**在正文中输出编号选项列表

## hook-03-07: Spec Conformance (契约一致性)
- **When**: post-coding
- **Rule**: 代码接口命名、入参、出参及异常处理必须与 `01-brainstorming.md` 的 Spec Contracts 保持 100% 一致。
- **Check**: 检查是否有针对 Spec Contracts 中所有 Scenario 的测试用例覆盖，且测试用例需全部通过。
