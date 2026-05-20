from sw_lib.agents.base import BaseAgent
from sw_lib.agents.opencode import OpenCodeAgent


def _agent(dummy_task):
    logs = []
    agent = OpenCodeAgent(
        {"add_log": lambda source, msg: logs.append((source, msg)), "is_running": lambda: True},
        dummy_task,
        "01-brainstorming",
        0,
        "opencode/deepseek-v4-flash-free",
    )
    return agent, logs


def test_handles_current_opencode_json_events(dummy_task):
    agent, logs = _agent(dummy_task)
    parts = []

    agent._handle_event({"type": "step-start", "snapshot": "abc"}, parts)
    agent._handle_event({"type": "text", "text": "hello from opencode"}, parts)
    agent._handle_event({"type": "step-finish", "reason": "stop"}, parts)

    assert agent._has_session is True
    assert agent.status == BaseAgent.STATUS_IDLE
    assert parts == ["hello from opencode"]
    assert ("agent", "hello from opencode") in logs
    assert any(msg == "✓ opencode 回复完成" for source, msg in logs if source == "sw")


def test_handles_current_opencode_tool_events(dummy_task):
    agent, logs = _agent(dummy_task)

    agent._handle_event(
        {
            "type": "tool",
            "tool": "read",
            "state": {
                "status": "completed",
                "input": {"filePath": "/tmp/example.txt"},
                "output": "content",
            },
        },
        [],
    )

    assert any(
        source == "system" and "read completed" in msg and "filePath=/tmp/example.txt" in msg
        for source, msg in logs
    )


def test_ignores_echoed_sent_message(dummy_task):
    agent, logs = _agent(dummy_task)
    parts = []

    agent._handle_event(
        {"type": "text", "text": "system prompt"},
        parts,
        sent_message="system prompt",
    )

    assert parts == []
    assert not logs


def test_continue_command_falls_back_without_session_id(dummy_task):
    agent, _ = _agent(dummy_task)

    cmd = agent._build_command("reply", is_continue=True)

    assert "-c" in cmd
    assert "-s" not in cmd
    assert cmd[-1] == "reply"
