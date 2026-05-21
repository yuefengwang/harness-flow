import json
from unittest.mock import MagicMock, patch

from urllib.error import URLError

from sw_lib.agents.base import BaseAgent
from sw_lib.agents.opencode import OpenCodeAgent


def _agent(dummy_task, model_name="opencode-go/deepseek-v4-flash"):
    logs = []
    agent = OpenCodeAgent(
        {
            "add_log": lambda source, msg: logs.append((source, msg)),
            "is_running": lambda: True,
            "on_complete": lambda: None,
        },
        dummy_task,
        "01-brainstorming",
        0,
        model_name,
    )
    return agent, logs


def _make_mock_urlopen(return_parts):
    body = json.dumps({
        "info": {"role": "assistant", "id": "msg_test", "sessionID": "ses_test"},
        "parts": return_parts,
    }).encode("utf-8")
    session_body = json.dumps({"id": "ses_mock_123"}).encode("utf-8")

    def urlopen_side_effect(request, **kwargs):
        resp = MagicMock()
        url = request.get_full_url() if hasattr(request, "get_full_url") else str(request)
        is_message_call = "/message" in url
        if is_message_call:
            resp.read.return_value = body
        else:
            resp.read.return_value = session_body
        cm = MagicMock()
        cm.__enter__.return_value = resp
        cm.__exit__.return_value = None
        return cm
    return urlopen_side_effect


# ── Model parsing ──────────────────────────────────────────────

def test_parse_model_for_http_splits_provider_and_model(dummy_task):
    agent, _ = _agent(dummy_task, "opencode-go/deepseek-v4-flash")
    assert agent._parse_model_for_http() == {"providerID": "opencode-go", "modelID": "deepseek-v4-flash"}


def test_parse_model_for_http_returns_none_for_opencode(dummy_task):
    agent, _ = _agent(dummy_task, "opencode")
    assert agent._parse_model_for_http() is None


def test_parse_model_for_http_single_part_model(dummy_task):
    agent, _ = _agent(dummy_task, "gemini-2.0-flash")
    assert agent._parse_model_for_http() is None


# ── _run_via_http response handling ────────────────────────────

def test_run_via_http_dispatches_text_parts(dummy_task):
    agent, logs = _agent(dummy_task)
    agent._server_url = "http://localhost:12345"
    agent._http_session_id = "ses_test123"

    with patch("sw_lib.agents.opencode.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_mock_urlopen([
            {"type": "step-start"},
            {"type": "text", "text": "Hello from HTTP"},
            {"type": "reasoning", "text": "thinking about greeting"},
            {"type": "step-finish", "reason": "stop"},
        ])
        agent._run_via_http("say hello", is_continue=True)

    assert agent.status == BaseAgent.STATUS_IDLE
    assert ("agent", "Hello from HTTP") in logs
    assert any("thinking" in msg for s, msg in logs if s == "system")


def test_run_via_http_empty_response_sets_error(dummy_task):
    agent, logs = _agent(dummy_task)
    agent._server_url = "http://localhost:12345"
    agent._http_session_id = "ses_test123"

    with patch("sw_lib.agents.opencode.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_mock_urlopen([
            {"type": "step-start"},
            {"type": "step-finish", "reason": "stop"},
        ])
        agent._run_via_http("test", is_continue=True)

    assert agent.status == BaseAgent.STATUS_ERROR
    assert any("空响应" in msg for _, msg in logs)


def test_run_via_http_creates_session_when_none(dummy_task):
    agent, logs = _agent(dummy_task)
    agent._server_url = "http://localhost:12345"
    agent._http_session_id = None

    call_count = [0]

    def urlopen_side_effect(request, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        if call_count[0] == 1:
            resp.read.return_value = json.dumps({"id": "ses_new456"}).encode()
        else:
            resp.read.return_value = json.dumps({
                "info": {"role": "assistant"},
                "parts": [
                    {"type": "step-start"},
                    {"type": "text", "text": "ok"},
                    {"type": "step-finish", "reason": "stop"},
                ],
            }).encode()
        cm = MagicMock()
        cm.__enter__.return_value = resp
        cm.__exit__.return_value = None
        return cm

    with patch("sw_lib.agents.opencode.urlopen", side_effect=urlopen_side_effect):
        agent._run_via_http("first message", is_continue=True)

    assert agent._http_session_id == "ses_new456"
    assert agent.status == BaseAgent.STATUS_IDLE


# ── _handle_http_tool_part ─────────────────────────────────────

def test_handle_http_tool_part_logs_tool_summary(dummy_task):
    agent, logs = _agent(dummy_task)
    agent._handle_http_tool_part({
        "name": "read_file",
        "input": {"filePath": "/tmp/test.txt"},
    })
    assert any("read_file" in msg and "filePath" in msg for _, msg in logs)


# ── _run_opencode branching ────────────────────────────────────

def test_run_opencode_uses_http_when_server_ready(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = "http://localhost:12345"
    agent._http_session_id = "ses_test123"

    with patch("sw_lib.agents.opencode.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = _make_mock_urlopen([
            {"type": "step-start"},
            {"type": "text", "text": "Hello"},
            {"type": "step-finish", "reason": "stop"},
        ])
        agent._run_opencode("test")

    assert agent.status == BaseAgent.STATUS_IDLE


def test_run_opencode_falls_back_to_cli_on_http_error(dummy_task):
    agent, logs = _agent(dummy_task)
    agent._server_url = "http://localhost:12345"
    agent._http_session_id = "ses_test123"

    with patch("sw_lib.agents.opencode.urlopen", side_effect=URLError("refused")), \
         patch("subprocess.Popen") as mock_popen, \
         patch("threading.Thread"), \
         patch.object(agent, "_stop_server"):
        mock_proc = MagicMock()
        mock_proc.stdout = []
        mock_proc.poll.return_value = 0
        mock_proc.stderr = []
        mock_popen.return_value = mock_proc
        agent._run_opencode("test")

    assert agent._server_url is None
    assert any("回退到 CLI" in msg for _, msg in logs)


def test_run_opencode_falls_back_to_cli_on_empty_response(dummy_task):
    agent, logs = _agent(dummy_task)
    agent._server_url = "http://localhost:12345"
    agent._http_session_id = "ses_test123"

    with patch("sw_lib.agents.opencode.urlopen") as mock_urlopen, \
         patch("subprocess.Popen") as mock_popen, \
         patch("threading.Thread"), \
         patch.object(agent, "_stop_server"):
        mock_urlopen.side_effect = _make_mock_urlopen([
            {"type": "step-start"},
            {"type": "step-finish", "reason": "stop"},
        ])
        mock_proc = MagicMock()
        mock_proc.stdout = []
        mock_proc.poll.return_value = 0
        mock_proc.stderr = []
        mock_popen.return_value = mock_proc
        agent._run_opencode("test")

    assert agent._server_url is None
    assert any("空响应" in msg for _, msg in logs)


def test_run_opencode_uses_cli_directly_when_no_server(dummy_task):
    agent, _ = _agent(dummy_task)
    agent._server_url = None

    with patch("subprocess.Popen") as mock_popen, \
         patch("threading.Thread"):
        mock_proc = MagicMock()
        mock_proc.stdout = []
        mock_proc.poll.return_value = 0
        mock_proc.stderr = []
        mock_popen.return_value = mock_proc

        agent._run_opencode("test message", is_continue=True)

    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert "opencode" in cmd
    assert "run" in cmd


# ── Original event handler tests ───────────────────────────────

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
