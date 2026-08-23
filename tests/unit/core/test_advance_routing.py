"""Phase 1 red tests: TaskService.advance_stage must delegate routing to
WorkflowRuntime.advance() and NOT reach into the executor's private
_stage_map / _stage_order attributes.

Run with: pytest tests/unit/core/test_advance_routing.py -v
"""
import pytest
from unittest.mock import patch
from sw_lib.core.service import TaskService
from sw_lib.core.state import read_state
from sw_lib.core.config import TASKS


@pytest.fixture
def prep_task(dummy_task):
    """Write a fresh state for dummy_task at the given stage."""
    def _set(stage, idx, status="pending"):
        from sw_lib.core.state import write_state
        write_state(dummy_task, {
            "id": dummy_task, "stage": stage, "stage_idx": idx,
            "stage_status": status, "agent": "cat",
        })
    return _set


class TestAdvanceDelegatesToRuntime:
    """Service must route through WorkflowRuntime.advance, not executor privates."""

    def test_linear_advance_via_runtime(self, dummy_task, prep_task):
        prep_task("01-brainstorming", 0)
        with patch("sw_lib.core.service.WorkflowRuntime") as RT:
            RT.advance.return_value = read_state(dummy_task)
            svc = TaskService()
            svc.advance_stage(dummy_task)
            RT.advance.assert_called_once_with(dummy_task)

    def test_runtime_returns_next_stage(self, dummy_task, prep_task):
        prep_task("01-brainstorming", 0)
        with patch("sw_lib.core.service.WorkflowRuntime") as RT:
            RT.advance.return_value = {"stage": "02-planning", "stage_idx": 1}
            svc = TaskService()
            res = svc.advance_stage(dummy_task)
            assert res["stage"] == "02-planning"
            assert res["stage_idx"] == 1


class TestRuntimeAdvanceLogic:
    """WorkflowRuntime.advance() is the single source of routing truth."""

    def test_last_stage_marks_finished(self, dummy_task, prep_task):
        prep_task("05-archive", 4)
        from sw_lib.workflow.runtime import WorkflowRuntime
        res = WorkflowRuntime.advance(dummy_task)
        assert res["stage_status"] == "Finished"

    def test_linear_progress(self, dummy_task, prep_task):
        prep_task("01-brainstorming", 0)
        (TASKS / dummy_task / "02-planning.md").write_text(
            "## Gate\n- [ ] t\n", encoding="utf-8")
        from sw_lib.workflow.runtime import WorkflowRuntime
        res = WorkflowRuntime.advance(dummy_task)
        assert res["stage"] == "02-planning"
        assert res["stage_idx"] == 1

    def test_review_route_to_earlier_stage(self, dummy_task, prep_task):
        prep_task("04-review", 3)
        # Route 决策存在 .state 里；写进 Markdown 的不算决定
        from sw_lib.workflow import stage_state as ss
        assert ss.write_route(dummy_task, "03-coding") is True
        from sw_lib.workflow.runtime import WorkflowRuntime
        res = WorkflowRuntime.advance(dummy_task)
        assert res["stage"] == "03-coding"
        assert res["stage_idx"] == 2

    def test_rerouted_stage_gate_reset(self, dummy_task, prep_task):
        prep_task("04-review", 3)
        from sw_lib.workflow import stage_state as ss
        ss.write_route(dummy_task, "03-coding")
        ss.sign_gate(dummy_task, "03-coding")
        from sw_lib.workflow.runtime import WorkflowRuntime
        WorkflowRuntime.advance(dummy_task)
        # 返工到的阶段必须回到未签署，否则旧签名会让它直接放行
        assert ss.read_gate(dummy_task, "03-coding").signed is False
