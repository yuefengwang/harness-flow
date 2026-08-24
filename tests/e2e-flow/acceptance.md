# 验收标准

> 每项标注 `[ ]` 为待验收，`[x]` 为已通过。
> 验收脚本 `verify.py` 会逐项检查，LLM 也可以手动确认。

---

## 通用验收（所有阶段）

- `[ ]` **阶段文件存在**: `workspace/tasks/<name>/<stage>.md` 存在
- `[ ]` **AI Output 产出**: 文件包含 `## 🤖 AI Output` 章节
- `[ ]` **模板 checkbox 已勾选**: `## Gate` 之前的所有 `[ ]` 模板 checkbox 已被勾选为 `[x]`。注意：AI Output 区域中 agent 输出的 `[ ]`（如 WBS 条目）不在此要求内
- `[ ]` **Gate checkbox 已勾选**: `## Gate` 下的所有 checkbox 均为 `[x]`
- `[ ]` **硬校验通过**: `hooks/check_<stage>.sh` 退出码为 0
- `[ ]` **阶段已推进**: `.state` 中 `stage` 已指向下一阶段（或 `stage_status` 为 `Finished`）
- `[ ]` **无异常日志**: `.log` 中不含 `error` 级别的异常（排除已知的预期警告）

---

## 01-brainstorming

- `[ ]` **Gate 设计批准**: `[x] Design approved` 存在
- `[ ]` **选择组已勾选**: 每个 Clarifying Question 下的 `[ ] A:` / `[ ] B:` 至少有一个被勾选
- `[ ]` **Ambiguity Score 已填写**: 模板中的 `___` 非必须填写（软校验不检查 blank），但建议填充
- `[ ]` **软校验无待办**: `check_stage_compliance()` 返回的 `todo_items` 为空
- `[ ]` **硬校验通过**: `check_01-brainstorming.sh` 退出码为 0
- `[ ]` **已推进到 02-planning**: `.state` 中 `stage` 为 `02-planning`

---

## 02-planning

- `[ ]` **Task DAG checkbox 已勾选**: `- [ ] **Task 1**` 等模板 checkbox 均为 `[x]`
- `[ ]` **Test Strategy 已填写**: `___` 占位符非必须填写
- `[ ]` **产出区按围栏定位**: 能从 `<!-- sw:ai-output:start … -->` 围栏内取到产出正文
  （不得按 `## 🤖 AI Output` 标题 split —— agent 正文里也会写同名标题）
- `[ ]` **WBS 条目保持原样**: 产出区中的 `1. [ ] **Task 1**` 等 `[ ]` 未被替换为 `[x]`，
  且**至少有一条**（一条都没有说明产出区取错或产出为空，不算通过）
- `[ ]` **硬校验通过**: `check_02-planning.sh` 退出码为 0
- `[ ]` **已推进到 03-coding**: `.state` 中 `stage` 为 `03-coding`

---

## 03-coding

- `[ ]` **所有模板 checkbox 已勾选**
- `[ ]` **硬校验通过**: `check_03-coding.sh` 退出码为 0
- `[ ]` **已推进到 04-review**: `.state` 中 `stage` 为 `04-review`

---

## 04-review

- `[ ]` **Security section 存在**: 文件包含 `## Security` 章节
- `[ ]` **Route 字段已填写**: `- **Route**: ``目标阶段`` 格式正确且值有效
- `[ ]` **Route 值有效**: 为 `05-Archive` / `03-Coding` / `02-Planning` / `01-Brainstorming` 之一
- `[ ]` **Reroute Evidence 表有数据**（仅当 Route 非 05-Archive 时）:
  - Evidence 表至少 1 行数据
  - 数据不含 `___` 占位符（shell hook 要求）
- `[ ]` **硬校验通过**: `check_04-review.sh` 退出码为 0
- `[ ]` **已推进到下一阶段**（或返工路由的目标阶段）

---

## 05-archive

- `[ ]` **所有模板 checkbox 已勾选**
- `[ ]` **硬校验通过**: `check_05-archive.sh` 退出码为 0
- `[ ]` **任务状态为 Finished**: `.state` 中 `stage_status` 为 `Finished`
- `[ ]` **结算界面已触发**: `.log` 中包含结算相关日志

---

## 最终验收

- `[ ]` **全部 5 阶段文件存在**并包含 AI Output
- `[ ]` **全部阶段已推进完成**（Finished）
- `[ ]` **日志中无未预期的 error 级别日志**
- `[ ]` **测试任务可正常移除**: `./sw remove --name=<name>` 成功
