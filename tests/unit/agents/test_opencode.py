"""Tests for stripped OpenCodeAgent — HTTP transport + callbacks."""
import json, threading
from unittest.mock import MagicMock, patch, ANY
from urllib.error import URLError

from sw_lib.agents.base import BaseAgent
from sw_lib.agents.opencode import OpenCodeAgent


def _agent(dummy_task, model_name="opencode-go/deepseek-v4-flash"):
    logs = []
    agent = OpenCodeAgent(
        {"add_log": lambda s, m: logs.append((s, m)),
         "is_running": lambda: True, "on_complete": lambda: None},
        dummy_task, "01-brainstorming", 0, model_name,
    )
    return agent, logs


def _mock_session_response():
    r = MagicMock()
    r.read.return_value = json.dumps({"id": "ses_mock_456"}).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = r
    cm.__exit__.return_value = None
    return cm


def _mock_message_response(parts):
    r = MagicMock()
    r.read.return_value = json.dumps({"info": {}, "parts": parts}).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = r
    cm.__exit__.return_value = None
    return cm


# ── Model parsing ──

def test_parse_model_param_splits(dummy_task):
    agent, _ = _agent(dummy_task, "opencode-go/deepseek-v4-flash")
    assert agent._parse_model_param() == {"providerID": "opencode-go", "modelID": "deepseek-v4-flash"}

def test_parse_model_param_none_for_opencode(dummy_task):
    agent, _ = _agent(dummy_task, "opencode")
    assert agent._parse_model_param() is None

def test_parse_model_param_single_part(dummy_task):
    agent, _ = _agent(dummy_task, "gemini-2.0-flash")
    assert agent._parse_model_param() is None


# ── HTTP send / dispatch ──

def test_send_http_dispatches_text(dummy_task):
    agent, logs = _agent(dummy_task)
    agent._server_url = "http://localhost:1"
    agent._http_session_id = "ses_test"

    callbacks = {"on_text": MagicMock(), "on_step_start": MagicMock(), "on_step_finish": MagicMock()}
    agent.callbacks.update(callbacks)

    with patch("sw_lib.agents.opencode.urlopen") as mock_u:
        mock_u.side_effect = [
            _mock_message_response([
                {"type": "step-start"},
                {"type": "text", "text": "Hello"},
                {"type": "step-finish", "reason": "stop"},
            ]),
        ]
        result = agent._send_http("hi", is_continue=True)
        agent._dispatch_parts(result["parts"])

    callbacks["on_text"].assert_called_with("Hello")
    callbacks["on_step_finish"].assert_called_with("stop")
    assert agent.status == BaseAgent.STATUS_IDLE


def test_send_http_tool_callback(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://localhost:1"
    agent._http_session_id = "ses_test"
    cb = MagicMock()
    agent.callbacks["on_tool"] = cb

    with patch("sw_lib.agents.opencode.urlopen") as mock_u:
        mock_u.side_effect = [_mock_message_response([
            {"type": "tool", "name": "read", "input": {"path": "f.txt"}},
            {"type": "step-finish", "reason": "stop"},
        ])]
        result = agent._send_http("x", is_continue=True)
        agent._dispatch_parts(result["parts"])

    cb.assert_called_once()


def test_send_http_retries_on_error(dummy_task):
    agent, logs = _agent(dummy_task)
    agent._server_url = "http://localhost:1"
    agent._http_session_id = "ses_test"

    with patch("sw_lib.agents.opencode.urlopen") as mock_u:
        mock_u.side_effect = [
            URLError("refused"),  # attempt 1
            URLError("refused"),  # attempt 2
            _mock_message_response([{"type": "step-finish", "reason": "stop"}]),
        ]
        result = agent._send_http("x", is_continue=True)

    assert result is not None
    assert any("HTTP error (attempt 1/3)" in msg for _, msg in logs)


def test_send_http_fails_after_max_retries(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://localhost:1"
    agent._http_session_id = "ses_test"

    with patch("sw_lib.agents.opencode.urlopen", side_effect=URLError("refused")):
        result = agent._send_http("x", is_continue=True)

    assert result is None
    assert agent._http_session_id is None  # broken session cleared


def test_send_http_creates_session_when_none(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://localhost:1"
    agent._http_session_id = None

    with patch("sw_lib.agents.opencode.urlopen") as mock_u:
        mock_u.side_effect = [
            _mock_session_response(),
            _mock_message_response([{"type": "step-finish", "reason": "stop"}]),
        ]
        agent._send_http("first message")

    assert agent._http_session_id == "ses_mock_456"


# ── send() lifecycle ──

def test_send_starts_server_and_sends(dummy_task):
    agent, logs = _agent(dummy_task)
    agent.running = True
    agent._server_url = "http://localhost:1"
    agent._http_session_id = "ses_test"

    with patch("sw_lib.agents.opencode.urlopen") as mock_u:
        mock_u.side_effect = [
            _mock_message_response([{"type": "step-finish", "reason": "stop"}]),
        ]
        agent.send("hello")

    # send() calls _dispatch_parts internally
    assert agent.status == BaseAgent.STATUS_IDLE


def test_send_no_server_sets_error(dummy_task):
    agent, _ = _agent(dummy_task)
    agent.running = True
    agent._server_url = None

    agent.send("hello")
    assert agent.status == BaseAgent.STATUS_ERROR


# ── Server lifecycle ──

def test_start_server(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = None

    with patch("subprocess.Popen") as mock_popen, \
         patch("sw_lib.agents.opencode.urlopen") as mock_u, \
         patch.object(agent, "_kill_orphaned_servers"):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc
        health = MagicMock()
        health.status = 200
        cm = MagicMock()
        cm.__enter__.return_value = health
        cm.__exit__.return_value = None
        mock_u.return_value = cm

        agent._start_server()

    assert agent._server_url is not None
    assert agent._server_url.startswith("http://127.0.0.1:")


def test_shutdown_kills_server(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://localhost:1"
    agent._server_proc = MagicMock()

    with patch.object(agent, "_kill_orphaned_servers"):
        agent.shutdown()

    assert agent._server_url is None
    assert agent._server_proc is None


def test_ignore_echoed_sent_message(dummy_task):
    """Text parts dispatched via on_text — echo filtering is caller's responsibility."""
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://localhost:1"
    agent._http_session_id = "ses_test"

    cb = MagicMock()
    agent.callbacks["on_text"] = cb

    with patch("sw_lib.agents.opencode.urlopen") as mock_u:
        mock_u.side_effect = [_mock_message_response([
            {"type": "text", "text": "echoed message"},
            {"type": "step-finish", "reason": "stop"},
        ])]
        result = agent._send_http("echoed message", is_continue=True)
        agent._dispatch_parts(result["parts"])

    cb.assert_called_once_with("echoed message")


def test_continue_command_falls_back_without_session_id(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://localhost:1"
    agent._http_session_id = None  # no session → creates new one

    with patch("sw_lib.agents.opencode.urlopen") as mock_u:
        mock_u.side_effect = [
            _mock_session_response(),
            _mock_message_response([{"type": "step-finish", "reason": "stop"}]),
        ]
        result = agent._send_http("msg", is_continue=True)

    assert result is not None
    assert agent._http_session_id is not None
