# 03-Coding Hooks (Simplicity & Surgical)

## hook-03-01: Surgical Updates (外科手术式改动)
- **When**: during
- **Rule**: only touch task-scoped code. No gratuitous refactoring. **只清理自己产生的垃圾**。
- **Check**: `git diff` shows only intended changes

## hook-03-02: Empirical Verification (目标驱动执行)
- **When**: during/post
- **Rule**: Red → Green required. Never assume fixes. **定义成功标准并循环验证**。
- **Check**: repro script fails before, passes after

## hook-03-02a: Red Witness (红由 harness 亲自观测)
- **When**: 03a 子阶段（尚未见证到红）
- **Rule**: **只写测试，不写实现**。测试必须因**断言失败**而红 ——
  引用不存在的模块（ImportError）不算红，那叫「造红」，会被门禁拒绝。
  收集不到测试、测试全部通过、测试全部 skip，同样无法见证。
- **Check**: harness 在准出时真实执行 pytest，要求退出码为 1 且有失败节点

## hook-03-02b: Frozen Tests (测试已冻结)
- **When**: 03b 子阶段（红已见证，正在写实现）
- **Rule**: **禁止修改任何测试文件**。它们的 sha256 已被冻结，改动会在准出时
  被检出并指名。若发现测试本身写错了，走这条**留痕**的出路，不得静默改：
  `python3 -m sw_lib.workflow.red_witness <task> --rewitness '<为什么要改>'`
  它把阶段退回 03a 并清掉冻结哈希，但**保留判据节点** ——
  回退后仍须重新见证到真实的红，不是跳过冻结的捷径。
- **Check**: 准出时重算哈希比对；且每个已见证的失败节点必须真的 `passed`
  —— `skipped` 不算绿

## hook-03-02c: Witness Is Not Optional (见证未发生 ≠ 已通过)
- **When**: 门禁打印「Red 见证未发生（unavailable）」时
- **Rule**: 那表示本阶段的红绿流程**没有被 harness 观测到**（存量任务、
  返工轮次、mock 模式或开关关闭）。它不是一次通过 —— 汇报时必须记为 ❓，
  **不得**写成「已完成红绿验证」。
- **Check**: `.state` 的 `red_witness.status == "unavailable"` 或 `mock == true`

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
