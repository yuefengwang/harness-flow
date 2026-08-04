"""Tests for OpenCodeAgent + OpenCodeTransport（选项 A 解耦层）。

核心断言：opencode 私有协议细节（parts 字段解析、端口/进程管理）只存在于
transport 层；OpenCodeAgent 只消费结构化 AgentMessage，不接触裸 parts / requests。
"""
import json
from unittest.mock import MagicMock, patch

from sw_lib.agents.base import BaseAgent
from sw_lib.agents.opencode import OpenCodeAgent
from sw_lib.agents.transport import OpenCodeTransport
from sw_lib.agents.protocol import AgentMessage, ToolCall


def _agent(dummy_task, model_name="opencode/deepseek-v4-flash-free"):
    logs = []
    agent = OpenCodeAgent(
        {"add_log": lambda s, m: logs.append((s, m)),
         "is_running": lambda: True, "on_complete": lambda: None},
        dummy_task, "01-brainstorming", 0, model_name,
    )
    return agent, logs


# ── Model parsing（仍属 agent 业务层，保留）──

def test_parse_model_splits(dummy_task):
    agent, _ = _agent(dummy_task, "opencode/deepseek-v4-flash-free")
    assert agent._parse_model() == ("opencode", "deepseek-v4-flash-free")


def test_parse_model_fallback_for_opencode(dummy_task):
    agent, _ = _agent(dummy_task, "opencode")
    prov, model = agent._parse_model()
    assert prov == "opencode"
    assert model == "deepseek-v4-flash-free"


def test_parse_model_fallback_for_empty(dummy_task):
    agent, _ = _agent(dummy_task, "")
    prov, model = agent._parse_model()
    assert prov == "opencode"
    assert model == "deepseek-v4-flash-free"


def test_parse_model_single_part(dummy_task):
    agent, _ = _agent(dummy_task, "gemini-2.0-flash")
    assert agent._parse_model() == ("opencode", "gemini-2.0-flash")


# ── transport 层：私有协议解析唯一入口 ──

def test_transport_parses_parts_into_structured_message():
    """opencode parts 协议只在 transport._to_message 解析。"""
    raw = {
        "parts": [
            {"type": "step-start"},
            {"type": "reasoning", "text": "thinking..."},
            {"type": "text", "text": "Hello"},
            {"type": "tool", "name": "read", "input": {"path": "f.txt"}},
            {"type": "step-finish", "reason": "stop"},
        ]
    }
    msg = OpenCodeTransport._to_message(raw)
    assert isinstance(msg, AgentMessage)
    assert msg.step_start is True
    assert msg.reasoning == "thinking..."
    assert msg.text == "Hello"
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0] == ToolCall(name="read", input={"path": "f.txt"})
    assert msg.finish_reason == "stop"


def test_transport_empty_parts_yields_idle_message():
    msg = OpenCodeTransport._to_message({"parts": []})
    assert msg.text == ""
    assert msg.has_tool is False
    assert msg.step_finish is False


# ── agent 层：只消费结构化 AgentMessage，不解析裸 parts ──

def test_agent_dispatch_text(dummy_task):
    agent, _ = _agent(dummy_task)
    msg = AgentMessage(text_parts=["Hello"], step_finish=True)

    callbacks = {"on_text": MagicMock(), "on_step_start": MagicMock(),
                 "on_step_finish": MagicMock()}
    agent.callbacks.update(callbacks)
    agent._dispatch_message(msg)

    callbacks["on_text"].assert_called_with("Hello")
    callbacks["on_step_finish"].assert_called_with("stop")
    assert agent.status == BaseAgent.STATUS_IDLE


def test_agent_dispatch_tool_callback(dummy_task):
    agent, _ = _agent(dummy_task)
    msg = AgentMessage(
        tool_calls=[ToolCall(name="read", input={"path": "f.txt"})],
        step_finish=True,
    )
    cb = MagicMock()
    agent.callbacks["on_tool"] = cb
    agent._dispatch_message(msg)
    cb.assert_called_once_with({"name": "read", "input": {"path": "f.txt"}})


def test_agent_send_via_transport(dummy_task):
    """send() 经由 transport.send_message 拿到结构化消息后分发。"""
    agent, _ = _agent(dummy_task)
    agent.running = True
    agent._transport._server_url = "http://127.0.0.1:9999"  # 模拟已 start

    msg = AgentMessage(text_parts=["done"], step_finish=True)
    with patch.object(agent._transport, "send_message", return_value=msg) as m:
        agent.send("hello")

    m.assert_called_once()
    assert agent.status == BaseAgent.STATUS_IDLE


def test_agent_send_no_transport_sets_error(dummy_task):
    agent, _ = _agent(dummy_task)
    agent.running = True

    with patch.object(agent._transport, "_server_url", None):
        agent.send("hello")
    assert agent.status == BaseAgent.STATUS_ERROR


def test_agent_send_transport_error_sets_error(dummy_task):
    agent, _ = _agent(dummy_task)
    agent.running = True
    from sw_lib.agents.transport import OpenCodeTransportError
    with patch.object(agent._transport, "send_message",
                      side_effect=OpenCodeTransportError("boom")):
        agent.send("hello")
    assert agent.status == BaseAgent.STATUS_ERROR


# ── MCP 工具注入（选项 A 关键能力）──

def test_mcp_config_written_when_enabled(dummy_task, tmp_path, monkeypatch):
    import sw_lib.agents.opencode as oc
    agent, _ = _agent(dummy_task)
    agent.use_mcp_tools = True
    monkeypatch.setattr(oc, "ROOT", tmp_path)
    path = agent._write_mcp_config()
    assert path is not None
    import os
    data = json.loads((tmp_path / ".mcp_harness.json").read_text(encoding="utf-8"))
    assert "harness-flow-tools" in data["mcpServers"]
    assert data["mcpServers"]["harness-flow-tools"]["args"] == [
        "-m", "sw_lib.agents.mcp_tools"]


def test_mcp_config_none_when_disabled(dummy_task):
    agent, _ = _agent(dummy_task)
    agent.use_mcp_tools = False
    assert agent._write_mcp_config() is None


# ── 生命周期 ──

def test_shutdown_delegates_to_transport(dummy_task):
    agent, _ = _agent(dummy_task)
    agent.running = True
    with patch.object(agent._transport, "shutdown") as m:
        agent.shutdown()
    m.assert_called_once()
    assert agent.running is False


def test_start_lazy_launches_transport(dummy_task):
    agent, _ = _agent(dummy_task)
    with patch.object(agent._transport, "start") as m:
        agent.start()
    m.assert_called_once()
    assert agent.running is True


def test_factory_enables_mcp_tools(dummy_task):
    """AgentFactory 构造的 opencode 应默认启用 MCP 工具桥接。"""
    from sw_lib.agents.base import AgentFactory
    agent = AgentFactory.create(
        "opencode",
        {"add_log": lambda *a: None, "is_running": lambda: True,
         "on_complete": lambda: None},
        dummy_task, "01-brainstorming", 0, "opencode",
    )
    assert isinstance(agent, OpenCodeAgent)
    assert agent.use_mcp_tools is True
