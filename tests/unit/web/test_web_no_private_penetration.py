"""Phase 3 red/green tests: Web must NOT reach into the executor's private
``active_stage.active_agent`` attributes. All interaction goes through the
WorkflowEngine abstraction.

Run with: pytest tests/unit/web/test_web_no_private_penetration.py -v
"""
import pytest
from unittest.mock import MagicMock
from sw_lib.web.engine_manager import WebEngineManager, WebEngineSession
from sw_lib.workflow.engine import WorkflowEngine


class FakeEngine(WorkflowEngine):
    """Isolated engine stub — proves the Web layer never touches executor internals."""
    def __init__(self):
        self.shutdown_calls = []
        self.status_value = "running"

    def start_stage(self, stage_input):
        pass

    def submit_answer(self, task_name, text):
        pass

    def submit_command(self, task_name, cmd):
        pass

    def shutdown(self, task_name):
        self.shutdown_calls.append(task_name)

    def get_status(self, task_name):
        return self.status_value


def test_status_sourced_from_engine_not_executor(dummy_task):
    """status must come from the engine abstraction, not executor.active_stage."""
    fake = FakeEngine()
    fake.status_value = "running"
    session = WebEngineSession(dummy_task, "01-brainstorming", 0, "N/A", engine=fake)
    assert session.status == "running"
    # No executor access happened
    assert session.engine is fake


def test_destroy_uses_engine_shutdown(dummy_task):
    fake = FakeEngine()
    session = WebEngineSession(dummy_task, "01-brainstorming", 0, "N/A", engine=fake)
    session.destroy()
    assert fake.shutdown_calls == [dummy_task]
    assert session.is_alive is False


def test_manager_injects_engine(dummy_task):
    mgr = WebEngineManager()
    session = mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    # engine must be a WorkflowEngine implementation, not a raw executor
    assert isinstance(session.engine, WorkflowEngine)
    mgr.destroy_session(dummy_task)


def test_status_without_executor_access(dummy_task, monkeypatch):
    """Even if WorkflowRuntime.get_executor() blows up, status still works
    via the engine (state-backed), proving no private penetration."""
    import sw_lib.workflow.runtime as rt_mod
    monkeypatch.setattr(rt_mod.WorkflowRuntime, "get_executor",
                        classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("no executor"))))
    fake = FakeEngine()
    fake.status_value = "idle"
    session = WebEngineSession(dummy_task, "01-brainstorming", 0, "N/A", engine=fake)
    # status is fully served by the engine; executor never consulted
    assert session.status == "idle"
