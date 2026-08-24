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
- **Rule**: 顺序是 **先写测试 → 让 harness 跑一次看到红 → 再写实现**。
  测试必须因**断言失败**而红 —— 引用不存在的模块（ImportError）不算红，
  那叫「造红」，会被门禁**拒绝**；收集不到测试、测试全部 skip、
  以及「测试自证（不依赖被测代码）却全绿」，同样被拒绝。
- **若实现已先落盘**：harness 不会拦你（它无法阻止经 `bash` 写文件），
  但会把本阶段如实记为 `unavailable`（❓）并**指名是哪些实现文件**，
  该记录一路进入 04 的审查事实与 05 的归档报告。
  **那不是通过** —— 此时测试的绿证明不了实现被验证过，
  因为没有任何断言曾经先失败。
- **Check**: harness 在准出时真实执行 pytest。见证到红要求退出码 1 且有失败
  节点；退出码 0 且存在实现文件时按绕过记录并放行（测试判定交回常规门禁）

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
- **Rule**: 那表示本阶段的红绿流程**没有被 harness 观测到**（实现先落盘、
  存量任务、返工轮次、非 Python 栈、mock 模式或开关关闭）。
  它不是一次通过 —— 汇报时必须记为 ❓，**不得**写成「已完成红绿验证」。
- **Check**: `.state` 的 `red_witness.status == "unavailable"`、
  `bypassed == true` 或 `mock == true`

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

## hook-03-07: README 是本阶段的交付物
- **When**: before stage exit
- **Rule**: 目标仓库根目录必须有 `README.md`，含项目用途、安装/启动方式、
  验证命令，且**不含** `TODO` / `___` / `FIXME` 占位符。
- **Check**: 由 04 的客观轨 O6 硬校验（`objective_check._readme_check`）。
- **为什么归 03**：03 是唯一同时拥有 `write_file` 与 `run_command` 的阶段。
  这条要求从前只写在 04 与 05 的 hooks 里，而 04 的 reviewer 当时没有写权限
  —— 要求在 04 兑现、能力只在 03 存在，任务 `qqqq` 因此在两次 `/advance`
  之间原地卡死（A0 的 2.9.11）。判据的**兑现阶段**必须与**能力所在阶段**一致。

## hook-03-06: User Interaction via Tool
- **When**: 需要用户确认、选择或输入时
- **Rule**: **必须**使用 `question` 工具，**禁止**在正文中输出编号选项列表
