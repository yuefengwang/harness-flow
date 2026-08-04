# 修复历史

> 每次修复 bug 后在此记录。格式：
> ```
> ## YYYY-MM-DD: 简要描述
> - **症状**: ...
> - **根因**: ...
> - **修复**: (文件名): (改动说明)
> - **验证**: driver.py + verify.py 全部通过
> ```

## 2026-06-29: Mock 模式 `/advance` 校验失败

- **症状**: TUI 中输入 `/advance` 后软校验/硬校验不通过，无法推进阶段
- **根因**:
  1. `[ ]`→`[x]` 全局替换只处理了 AI Output 之前的模板区，漏掉了 `## Gate` section 的 checkbox → Gate 中的 `[ ] Design approved` 未被勾选 → 硬校验 `check_01-brainstorming.sh` 的 `grep -q "\[x\] Design approved"` 失败
  2. 全局替换同时误伤了 AI Output 区内的 WBS `[ ]` 条目
- **修复** (`sw_lib/runnable/base.py` `_save_stage_output()`): 三分区替换策略
  - 模板区（AI Output 前）: `[ ]`→`[x]`
  - AI Output 区（`## 🤖 AI Output` → `## Gate`）: 保持原样
  - Gate 区（`## Gate` 后）: `[ ]`→`[x]`
- **验证**: `tests/e2e-flow/driver.py` + `verify.py` 全部通过

## 2026-06-29: 软校验误计 AI Output 区 WBS checkbox

- **症状**: 02-planning 模板 checkbox 全部已勾选 `[x]`，但软校验报 "3 个待填项未完成"
- **根因**: `check_stage_compliance()` 遍历文件全部行统计 checkbox，把 AI Output 区中 WBS 条目 `1. [ ] 定义数据模型` 等也算入了"待填项"
- **修复** (`sw_lib/runnable/utils.py` `check_stage_compliance()`): 增加 AI Output 区域跳过逻辑，遇到 `## 🤖 AI Output` 后跳过该区域直到 `## Gate`，之后继续检查 Gate 区的 checkbox
- **验证**: `tests/e2e-flow/driver.py` + `verify.py` 全部通过

## 2026-06-29: Review 阶段 Route 字段未填写

- **症状**: 04-review 阶段 `/advance` 时报 "Route 字段尚未填写" 和硬校验失败
- **根因**: MockAgent review 场景在 AI Output 中写入了 `**建议路由: 05-Archive**`，但未填写模板中的 `- **Route**: ``___``` 字段
- **修复**: `tests/e2e-flow/driver.py` 在 review 阶段 Agent 完成后、`/advance` 前，直接写入 Route 字段
- **验证**: `tests/e2e-flow/driver.py` + `verify.py` 全部通过
