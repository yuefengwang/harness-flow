"""Phase 4 — verify dead-code symbols are gone.

RerouteLimitExceeded (never raised/caught) and WorkflowExecutor (unused
Protocol, lived alone in executor.py) were deleted. These must NOT be
reintroduced via the sw_lib.workflow public surface.

Run with: pytest tests/unit/workflow/test_dead_code_removed.py -v
"""
import pytest
import sw_lib.workflow as wf


def test_reroutelimitexceeded_not_exported():
    assert not hasattr(wf, "RerouteLimitExceeded")
    with pytest.raises(ImportError):
        from sw_lib.workflow.base import RerouteLimitExceeded  # noqa: F401


def test_workflow_executor_protocol_not_exported():
    assert not hasattr(wf, "WorkflowExecutor")
    with pytest.raises(ImportError):
        from sw_lib.workflow.executor import WorkflowExecutor  # noqa: F401


def test_core_symbols_still_present():
    for sym in ("StageRunnable", "StageInput", "StageOutput", "LangGraphAdapter", "GateValidator"):
        assert hasattr(wf, sym), f"regression: missing {sym}"
