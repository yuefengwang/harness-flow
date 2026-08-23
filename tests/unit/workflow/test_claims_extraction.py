"""A3 的 3.5 遗留：03 准出必须真的调用 record_claims()。

A3 交付时 `record_claims()` 有实现有测试，但**没有任何调用方** ——
于是 `claims` 始终为空，验收 18 的 claims-vs-diff 对照在真实运行中从未触发。

方案已拍板：**由 agent 在 03 产出里结构化声明**，harness 从产出解析。
不从 diff 反推 —— 那会让「声明对照 diff」变成 diff 自己跟自己比，
对照就失去了全部意义。

所以这里的判据分两层：
  1. 解析器能从 03 产出里取出 task_ids / verify_cmd / files_touched。
  2. 03 阶段跑完后 `.state` 里真的有 claims（接线，不只是函数可用）。
"""

import pytest

from sw_lib.workflow import fact_pack as FP


# ── 第一层：解析 ──

SAMPLE = """
## Task
`A5-3`: 让审查者数量可配

## Red-Green
- [x] **Red**: repro/test fails
- [x] **Green**: fix passes
- **Verify cmd**: `python3 -m pytest tests/unit/workflow/test_review_subgraph.py`

## Files Touched
- `sw_lib/workflow/review_graph.py`
- `sw_lib/workflow/graph.py`

## Implementation Notes
- **Pattern**: fan-out / fan-in
"""


def test_extracts_task_id():
    claims = FP.extract_claims(SAMPLE)
    assert claims["task_ids"] == ["A5-3"]


def test_extracts_verify_cmd():
    claims = FP.extract_claims(SAMPLE)
    assert claims["verify_cmd"] == \
        "python3 -m pytest tests/unit/workflow/test_review_subgraph.py"


def test_extracts_files_touched():
    """files_touched 是 claims-vs-diff 对照的核心字段。"""
    claims = FP.extract_claims(SAMPLE)
    assert claims["files_touched"] == [
        "sw_lib/workflow/review_graph.py", "sw_lib/workflow/graph.py"]


def test_unfilled_placeholders_are_not_claims():
    """模板占位符 `___` 不得被当成真实声明。

    把 `___` 记成一个文件名会让对照凭空多出一条不存在的声明。
    """
    claims = FP.extract_claims("""
## Task
`[Task ID]`: ___

- **Verify cmd**: `___`

## Files Touched
- ___
""")
    assert claims["task_ids"] == []
    assert claims["verify_cmd"] == ""
    assert claims["files_touched"] == []


def test_missing_sections_yield_empty_not_crash():
    claims = FP.extract_claims("完全没有结构的一段文字")
    assert claims == {"task_ids": [], "verify_cmd": "", "files_touched": []}


def test_backtick_and_plain_file_forms_both_parsed():
    """有人写反引号有人不写，两种都得认。"""
    claims = FP.extract_claims("""
## Files Touched
- `a/b.py`
- c/d.py
""")
    assert claims["files_touched"] == ["a/b.py", "c/d.py"]


# ── 第二层：接线 ──

def test_coding_stage_records_claims_into_state(tmp_path, monkeypatch):
    """03 跑完后 .state 必须真的有 claims。

    这条是 A3 遗留缺口的真身：函数可用 != 有人调用。

    ⚠️ 必须用**真实文件结构**（模板 + 围栏产出区），不能只喂裸产出：
    真实的 03-coding.md 里模板的空占位段排在 AI 产出**之前**，
    喂裸产出的测试看不到「先匹配到占位符就取不到真实声明」这个 bug。
    """
    import sw_lib.core.config as C
    import sw_lib.core.state as S
    import sw_lib.workflow.base as B
    import sw_lib.workflow.stage_state as SS
    from unittest.mock import MagicMock

    task = "rw-claims-wiring"
    for mod in (C, S, SS, B, FP):
        monkeypatch.setattr(mod, "TASKS", tmp_path, raising=False)
    (tmp_path / task).mkdir(parents=True)
    # 真实起点：带空占位符的模板。
    (tmp_path / task / "03-coding.md").write_text(
        (C.TPLS / "03-coding.md").read_text(encoding="utf-8"), encoding="utf-8")
    S.write_state(task, {"task": task, "stage": "03-coding", "stage_idx": 2})

    from sw_lib.workflow.base import StageRunnable
    r = StageRunnable(stage="03-coding", stage_idx=2, context_builder=MagicMock(),
                      output_parser=MagicMock(), gate_validator=MagicMock(),
                      agent_factory=MagicMock())
    r._save_stage_output(task, SAMPLE)

    claims = FP.read_claims(task)
    assert claims.get("task_ids") == ["A5-3"], f"claims 未落盘：{claims}"
    assert claims.get("verify_cmd") == \
        "python3 -m pytest tests/unit/workflow/test_review_subgraph.py", \
        f"verify_cmd 被模板占位符抢先匹配了：{claims}"
    assert claims.get("files_touched") == [
        "sw_lib/workflow/review_graph.py", "sw_lib/workflow/graph.py"]


def test_other_stages_do_not_write_claims(tmp_path, monkeypatch):
    """claims 是 03 的概念，别的阶段不该写。"""
    import sw_lib.core.config as C
    import sw_lib.core.state as S
    import sw_lib.workflow.base as B
    import sw_lib.workflow.stage_state as SS
    from unittest.mock import MagicMock

    task = "rw-claims-other"
    for mod in (C, S, SS, B, FP):
        monkeypatch.setattr(mod, "TASKS", tmp_path, raising=False)
    (tmp_path / task).mkdir(parents=True)
    S.write_state(task, {"task": task, "stage": "01-brainstorming", "stage_idx": 0})

    from sw_lib.workflow.base import StageRunnable
    r = StageRunnable(stage="01-brainstorming", stage_idx=0, context_builder=MagicMock(),
                      output_parser=MagicMock(), gate_validator=MagicMock(),
                      agent_factory=MagicMock())
    r._save_stage_output(task, SAMPLE)

    assert FP.read_claims(task) == {}
