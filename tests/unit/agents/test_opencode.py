"""Tests for HTTP-based OpenCodeAgent."""
import json
from unittest.mock import MagicMock, patch

from sw_lib.agents.base import BaseAgent
from sw_lib.agents.opencode import OpenCodeAgent


def _agent(dummy_task, model_name="opencode/deepseek-v4-flash-free"):
    logs = []
    agent = OpenCodeAgent(
        {"add_log": lambda s, m: logs.append((s, m)),
         "is_running": lambda: True, "on_complete": lambda: None},
        dummy_task, "01-brainstorming", 0, model_name,
    )
    return agent, logs


def _mock_requests_response(json_data, status_code=200):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


# ── Model parsing ──

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


# ── send_via_http / dispatch ──

def test_send_via_http_dispatches_text(dummy_task):
    agent, logs = _agent(dummy_task)
    agent._server_url = "http://127.0.0.1:54321"
    agent._session_id = "ses_test"

    chat_resp = _mock_requests_response({
        "info": {},
        "parts": [
            {"type": "step-start"},
            {"type": "text", "text": "Hello"},
            {"type": "step-finish", "reason": "stop"},
        ],
    })

    callbacks = {"on_text": MagicMock(), "on_step_start": MagicMock(), "on_step_finish": MagicMock()}
    agent.callbacks.update(callbacks)

    with patch("sw_lib.agents.opencode.requests.post", return_value=chat_resp):
        parts = agent._send_via_http("hi")
        agent._dispatch_parts(parts)

    callbacks["on_text"].assert_called_with("Hello")
    callbacks["on_step_finish"].assert_called_with("stop")
    assert agent.status == BaseAgent.STATUS_IDLE


def test_send_via_http_tool_callback(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://127.0.0.1:54321"
    agent._session_id = "ses_test"

    chat_resp = _mock_requests_response({
        "info": {},
        "parts": [
            {"type": "tool", "name": "read", "input": {"path": "f.txt"}},
            {"type": "step-finish", "reason": "stop"},
        ],
    })

    cb = MagicMock()
    agent.callbacks["on_tool"] = cb

    with patch("sw_lib.agents.opencode.requests.post", return_value=chat_resp):
        parts = agent._send_via_http("x")
        agent._dispatch_parts(parts)

    cb.assert_called_once()


def test_send_creates_session_on_first_call(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://127.0.0.1:54321"
    agent._session_id = None

    session_resp = _mock_requests_response({"id": "ses_mock_http_xxx"})
    chat_resp = _mock_requests_response({
        "info": {},
        "parts": [{"type": "step-finish", "reason": "stop"}],
    })

    with patch("sw_lib.agents.opencode.requests.post", side_effect=[session_resp, chat_resp]):
        parts = agent._send_via_http("first msg")

    assert agent._session_id == "ses_mock_http_xxx"
    assert parts is not None


def test_send_reuses_session(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://127.0.0.1:54321"
    agent._session_id = "ses_existing"

    chat_resp = _mock_requests_response({
        "info": {},
        "parts": [{"type": "step-finish", "reason": "stop"}],
    })

    with patch("sw_lib.agents.opencode.requests.post", return_value=chat_resp):
        agent._send_via_http("another msg")

    # session create should NOT be called
    with patch("sw_lib.agents.opencode.requests.post") as mock_post:
        mock_post.return_value = chat_resp
        agent._send_via_http("another msg")
        assert mock_post.call_count == 1
        assert "/session/ses_existing/message" in mock_post.call_args[0][0]


# ── send() lifecycle ──

def test_send_completes_ok(dummy_task):
    agent, logs = _agent(dummy_task)
    agent.running = True
    agent._server_url = "http://127.0.0.1:54321"
    agent._session_id = "ses_test"

    chat_resp = _mock_requests_response({
        "info": {},
        "parts": [{"type": "step-finish", "reason": "stop"}],
    })

    with patch("sw_lib.agents.opencode.requests.post", return_value=chat_resp):
        agent.send("hello")

    assert agent.status == BaseAgent.STATUS_IDLE


def test_send_no_server_sets_error(dummy_task):
    agent, _ = _agent(dummy_task)
    agent.running = True
    agent._server_url = None

    agent.send("hello")
    assert agent.status == BaseAgent.STATUS_ERROR


def test_requests_connection_error_sets_error(dummy_task):
    import requests as req_lib
    agent, _ = _agent(dummy_task)
    agent.running = True
    agent._server_url = "http://127.0.0.1:54321"
    agent._session_id = "ses_test"

    with patch("sw_lib.agents.opencode.requests.post", side_effect=req_lib.ConnectionError("refused")):
        agent.send("hello")

    assert agent.status == BaseAgent.STATUS_ERROR


def test_requests_timeout_logs_timeout(dummy_task):
    import requests as req_lib
    agent, logs = _agent(dummy_task)
    agent.running = True
    agent._server_url = "http://127.0.0.1:54321"
    agent._session_id = "ses_test"

    with patch("sw_lib.agents.opencode.requests.post", side_effect=req_lib.Timeout("timed out")):
        agent.send("hello")

    assert agent.status == BaseAgent.STATUS_ERROR
    assert any("HTTP timeout:" in msg for _, msg in logs)
    assert not any("HTTP connection error:" in msg for _, msg in logs)


# ── Server lifecycle ──

def test_start_server(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = None

    session_resp = _mock_requests_response({"id": "ses_health_check"})

    with patch("subprocess.Popen") as mock_popen, \
         patch("sw_lib.agents.opencode.requests.post", return_value=session_resp) as mock_post, \
         patch.object(agent, "_cleanup_orphans"), \
         patch.object(agent, "_find_free_port", return_value=54321):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        agent._start_server()

    assert agent._server_port is not None
    assert agent._server_url == "http://127.0.0.1:54321"
    assert OpenCodeAgent.CHAT_TIMEOUT >= 1800.0
    mock_post.assert_called_once_with(
        "http://127.0.0.1:54321/session",
        timeout=agent.HEALTH_TIMEOUT,
    )


def test_shutdown_aborts_session(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://127.0.0.1:54321"
    agent._session_id = "ses_mock"
    agent._server_proc = MagicMock()

    with patch("sw_lib.agents.opencode.requests.post") as mock_post:
        agent.shutdown()

    mock_post.assert_called_once_with(
        "http://127.0.0.1:54321/session/ses_mock/abort",
        timeout=10,
    )
    assert agent._session_id is None


def test_ignore_echoed_sent_message(dummy_task):
    """Text parts dispatched via on_text — echo filtering is caller's responsibility."""
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://127.0.0.1:54321"
    agent._session_id = "ses_test"

    chat_resp = _mock_requests_response({
        "info": {},
        "parts": [
            {"type": "text", "text": "echoed message"},
            {"type": "step-finish", "reason": "stop"},
        ],
    })

    cb = MagicMock()
    agent.callbacks["on_text"] = cb

    with patch("sw_lib.agents.opencode.requests.post", return_value=chat_resp):
        parts = agent._send_via_http("echoed message")
        agent._dispatch_parts(parts)

    cb.assert_called_once_with("echoed message")
