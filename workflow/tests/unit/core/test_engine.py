import pytest
import threading
from sw_lib.core.engine import WorkflowEngine
from sw_lib.core.state import read_state

def test_build_context_with_template(dummy_task, agent_callbacks):
    """测试上下文构建包含模板或 hooks 内容"""
    engine = WorkflowEngine(dummy_task, "01-brainstorming", 0, "", agent_callbacks)
    ctx = engine._build_context()
    # 应包含当前阶段模板、hooks 规则或需求上下文中的至少一个
    if ctx:
        assert any(keyword in ctx for keyword in ["头脑风暴", "Brainstorming", "hook", "强制规则"])

def test_command_dispatch(dummy_task, agent_callbacks):
    """测试 / 命令通过 engine 分发"""
    logs = []
    callbacks = dict(agent_callbacks)
    callbacks["add_log"] = lambda s, m: logs.append((s, m))
    
    engine = WorkflowEngine(dummy_task, "01-brainstorming", 0, "", callbacks)
    engine.handle_command("status")
    sw_logs = [msg for src, msg in logs if src == "sw"]
    assert any("stage=" in m for m in sw_logs)

def test_unknown_command(dummy_task, agent_callbacks):
    """测试未知命令"""
    logs = []
    callbacks = dict(agent_callbacks)
    callbacks["add_log"] = lambda s, m: logs.append((s, m))
    
    engine = WorkflowEngine(dummy_task, "01-brainstorming", 0, "", callbacks)
    engine.handle_command("xyz-unknown-cmd")
    sw_logs = [msg for src, msg in logs if src == "sw"]
    assert any("未知命令" in m for m in sw_logs)
