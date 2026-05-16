import pytest
from sw_lib.core.engine import WorkflowEngine

def test_build_context_with_template(dummy_task, agent_callbacks):
    engine = WorkflowEngine(dummy_task, "01-brainstorming", 0, "", agent_callbacks)
    ctx = engine._build_context()
    if ctx:
        assert any(keyword in ctx for keyword in ["头脑风暴", "Brainstorming", "hook", "强制规则"])

def test_command_dispatch(dummy_task, agent_callbacks):
    logs = []
    callbacks = dict(agent_callbacks)
    callbacks["add_log"] = lambda s, m: logs.append((s, m))
    engine = WorkflowEngine(dummy_task, "01-brainstorming", 0, "", callbacks)
    engine.handle_command("status")
    sw_logs = [msg for src, msg in logs if src == "sw"]
    assert any("stage=" in m for m in sw_logs)
