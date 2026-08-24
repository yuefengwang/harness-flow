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

## 2026-08-24: MockAgent 02/05 产出过薄 + 一条恒真的验收项

- **症状**:
  1. 给 01/02 补上「产出区必须有实质内容」的硬校验后，e2e 挂在 02-planning：
     去噪后仅 65 字符，阈值 80。
  2. 修完继续跑，`WBS items preserved` 打印「0 unchecked WBS items」——
     而 mock 明明输出了 3 条 WBS 条目。
- **根因**:
  1. MockAgent 的 02 产出只有三行 WBS 标题、05 落在 `_scenario_generic`
     一句「工作已顺利完成」收工（46 字符）。**薄的是 mock，不是阈值太高**：
     阈值 80 由实测校准（真实产出 200+ / 空转 <30），降它去迎合 mock
     属于「放宽标准让存量变绿」（A6 的 9.3 禁止）。
  2. 那条验收项有两个 bug 叠加：`ok` 参数写死成字面量 `True`（数出几条都记
     `[✓]`）；取产出区用 `split("## 🤖 AI Output")[1]`，而该标题在文件里出现
     两次（sw 写的 + agent 正文里自己写的），`[1]` 只是中间那行围栏注释。
     两个 bug 合起来让判据恒真，与本轮修掉的 `grep -q "## Task DAG"` 同类。
- **修复**:
  - `sw_lib/agents/mock.py`: 02 产出补齐 Task DAG（含 Deps/Do/Verify）、
    Test Strategy、Tech Detail 三段（对齐 `fact_pack.build_plan` 的提取标题）；
    新增 `_scenario_archive` 产出 Summary/Memory/Retro。WBS 条目保持 `[ ]`。
  - `tests/e2e-flow/verify.py`: 新增 `output_region()` 按围栏 nonce 取产出区；
    WBS 判据改为「至少一条未勾选且零条已勾选」的真判断。
  - 判据：`tests/unit/agents/test_mock_output_substance.py`（14 项，复用
    `check_output` 本身当尺子并要求 20% 余量）、
    `tests/unit/agents/test_e2e_verify_no_tautology.py`（3 项，源码级扫描
    `self.check` 的 `ok` 不得为字面量 `True`）。
- **验证**: `driver.py` 45/45 通过（原 44 项，新增「产出区按围栏定位」一项）；
  `python3 -m pytest tests/unit -q` 1428 passed
