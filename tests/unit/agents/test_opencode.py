"""Tests for OpenCodeAgent + OpenCodeTransport（选项 A 解耦层）。

核心断言：opencode 私有协议细节（parts 字段解析、端口/进程管理）只存在于
transport 层；OpenCodeAgent 只消费结构化 AgentMessage，不接触裸 parts / requests。
"""
from unittest.mock import MagicMock, patch

from sw_lib.agents.base import BaseAgent
from sw_lib.agents.opencode import OpenCodeAgent
from sw_lib.agents.transport import OpenCodeTransport
from sw_lib.agents.protocol import AgentMessage, ToolCall


def _agent(dummy_task, model_name="opencode/nemotron-3.5-lightning-free"):
    logs = []
    agent = OpenCodeAgent(
        {"add_log": lambda s, m: logs.append((s, m)),
         "is_running": lambda: True, "on_complete": lambda: None},
        dummy_task, "01-brainstorming", 0, model_name,
    )
    return agent, logs


# ── Model parsing（仍属 agent 业务层，保留）──

def test_parse_model_splits(dummy_task):
    agent, _ = _agent(dummy_task, "opencode/nemotron-3-ultra-free")
    assert agent._parse_model() == ("opencode", "nemotron-3-ultra-free")


def test_parse_model_fallback_for_opencode(dummy_task):
    from sw_lib.agents.opencode import DEFAULT_MODEL, FREE_MODELS
    agent, _ = _agent(dummy_task, "opencode")
    prov, model = agent._parse_model()
    assert prov == "opencode"
    assert model == DEFAULT_MODEL
    assert model in FREE_MODELS


def test_parse_model_fallback_for_empty(dummy_task):
    from sw_lib.agents.opencode import DEFAULT_MODEL
    agent, _ = _agent(dummy_task, "")
    prov, model = agent._parse_model()
    assert prov == "opencode"
    assert model == DEFAULT_MODEL


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
            {"type": "tool", "tool": "read",
             "state": {"status": "completed", "input": {"path": "f.txt"}}},
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


# ── 真实 opencode HTTP 契约（依据 GET /doc 与实测）──

def test_send_payload_uses_model_object_and_parts(dummy_task):
    """model 必须是 {providerID, modelID} 对象、正文必须走 parts；
    传字符串会被 opencode 以 HTTP 400 拒绝。"""
    agent, _ = _agent(dummy_task, "opencode/nemotron-3.5-lightning-free")
    agent.running = True
    t = agent._transport
    t._server_url = "http://127.0.0.1:9999"
    t._session_id = "ses_x"

    with patch("sw_lib.agents.transport.requests.post") as post:
        post.return_value = MagicMock(
            ok=True, raise_for_status=lambda: None, json=lambda: {"parts": []})
        t.send_message("hello")

    payload = post.call_args.kwargs["json"]
    assert payload["model"] == {"providerID": "opencode",
                                "modelID": "nemotron-3.5-lightning-free"}
    assert payload["parts"] == [{"type": "text", "text": "hello"}]
    assert "history" not in payload


def test_transport_parses_native_tool_part_shape():
    """opencode 1.17.x 的 tool part 用 `tool` + `state.input`。"""
    msg = OpenCodeTransport._to_message({"parts": [
        {"type": "tool", "tool": "bash",
         "state": {"status": "completed", "input": {"command": "pytest"}}},
    ]})
    assert msg.tool_calls == [ToolCall(name="bash", input={"command": "pytest"})]


def test_transport_start_rejects_missing_binary(monkeypatch):
    """opencode 不在 PATH 时必须明确报错，而不是静默挂起。"""
    from sw_lib.agents.transport import OpenCodeTransportError
    monkeypatch.setattr("sw_lib.agents.transport.shutil.which", lambda _: None)
    t = OpenCodeTransport()
    try:
        t.start()
    except OpenCodeTransportError as e:
        assert "opencode" in str(e)
    else:
        raise AssertionError("expected OpenCodeTransportError")


def test_start_failure_sets_error_status_and_logs(dummy_task):
    """启动失败要冒泡到 UI 状态与日志，避免界面永远停在启动中。"""
    from sw_lib.agents.transport import OpenCodeTransportError
    agent, logs = _agent(dummy_task)
    with patch.object(agent._transport, "start",
                      side_effect=OpenCodeTransportError("boom")):
        agent.start()
    assert agent.status == BaseAgent.STATUS_ERROR
    assert agent.running is False
    assert any(src == "error" for src, _ in logs)


def test_send_failure_still_fires_on_complete(dummy_task):
    """失败也必须触发 on_complete，否则 workflow 等到超时才醒。"""
    from sw_lib.agents.transport import OpenCodeTransportError
    done = MagicMock()
    agent, _ = _agent(dummy_task)
    agent.callbacks["on_complete"] = done
    agent.running = True
    agent._transport._server_url = "http://127.0.0.1:9999"
    with patch.object(agent._transport, "send_message",
                      side_effect=OpenCodeTransportError("boom")):
        agent.send("hi")
    done.assert_called_once()


def test_tool_switches_follow_stage_permissions(dummy_task):
    """只读阶段不得放开 bash / write。"""
    import sw_lib.agents.opencode as oc
    agent, _ = _agent(dummy_task)
    with patch.object(oc, "get_tools_for_stage",
                      return_value=["list_files", "read_file"]):
        sw = agent._tool_switches()
    assert sw["read"] is True and sw["list"] is True
    assert sw["bash"] is False and sw["write"] is False

    with patch.object(oc, "get_tools_for_stage",
                      return_value=["read_file", "write_file", "run_command"]):
        sw = agent._tool_switches()
    assert sw["bash"] is True and sw["write"] is True


def test_session_pinned_to_workdir(dummy_task):
    """session 必须钉在代码目录，否则 agent 读不到项目文件。"""
    agent, _ = _agent(dummy_task)
    assert agent.workdir
    assert agent._transport.directory == agent.workdir
    assert agent._transport._params()["directory"] == agent.workdir


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


def test_factory_enables_native_tools(dummy_task):
    """AgentFactory 构造的 opencode 应默认启用原生工具。"""
    from sw_lib.agents.base import AgentFactory
    agent = AgentFactory.create(
        "opencode",
        {"add_log": lambda *a: None, "is_running": lambda: True,
         "on_complete": lambda: None},
        dummy_task, "01-brainstorming", 0, "opencode",
    )
    assert isinstance(agent, OpenCodeAgent)
    assert agent.use_native_tools is True


# ── 凭证环境变量必须传到 serve 子进程 ──

def test_agent_hands_credentials_env_to_transport(dummy_task):
    """_load_env 的结果必须交给 transport，否则 API key 到不了 server。"""
    agent, _ = _agent(dummy_task)
    assert agent._transport.env is agent._env
    assert agent._env, "凭证 env 不应为空（至少继承 os.environ）"


def test_transport_start_passes_env_to_subprocess(monkeypatch):
    """serve 子进程必须收到 env；否则 credentials.yaml 里的 key 静默失效。"""
    sentinel = {"OPENAI_API_KEY": "sk-test", "PATH": "/usr/bin"}
    captured = {}

    class _FakeProc:
        stdout = iter(["opencode server listening on http://127.0.0.1:49373\n"])

    def _fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr("sw_lib.agents.transport.shutil.which", lambda _: "/usr/bin/opencode")
    monkeypatch.setattr("sw_lib.agents.transport.subprocess.Popen", _fake_popen)

    t = OpenCodeTransport(env=sentinel)
    t._await_listening = lambda proc: "http://127.0.0.1:49373"
    t._drain_logs_async = lambda proc: None
    t.start()

    assert captured.get("env") == sentinel


# ── 原生 question 工具：不应答就会让 POST /message 挂到 CHAT_TIMEOUT ──

def _asked_event(qid="que_1", sid="ses_x", questions=None):
    import json
    return "data: " + json.dumps({
        "type": "question.asked",
        "properties": {
            "id": qid,
            "sessionID": sid,
            "questions": questions if questions is not None else [{
                "question": "核心目标是什么？",
                "header": "业务目标",
                "options": [{"label": "通用电商平台", "description": "多品类"},
                            {"label": "最小可行商城", "description": "MVP"}],
            }],
        },
    })


def _pump_events(transport, lines, **cbs):
    """驱动 start_events 的 SSE pump，返回后确保 pump 线程已收完所有行。"""
    resp = MagicMock()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: False
    resp.iter_lines = lambda decode_unicode=True: iter(lines)
    with patch("sw_lib.agents.transport.requests.get", return_value=resp):
        transport.start_events(**cbs)
        transport._event_thread.join(timeout=5)
    return resp


def test_sse_question_asked_routes_to_callback():
    """question.asked 必须冒泡给 on_question；否则没人知道 agent 在等回答。"""
    t = OpenCodeTransport()
    t._server_url = "http://127.0.0.1:9999"
    t._session_id = "ses_x"
    seen = []
    _pump_events(t, [_asked_event()], on_question=lambda qid, qs: seen.append((qid, qs)))
    assert len(seen) == 1
    assert seen[0][0] == "que_1"
    assert seen[0][1][0]["header"] == "业务目标"


def test_sse_question_ignores_other_sessions():
    """/event 是全局流，不能把别的会话的提问抢过来应答。"""
    t = OpenCodeTransport()
    t._server_url = "http://127.0.0.1:9999"
    t._session_id = "ses_mine"
    seen = []
    _pump_events(t, [_asked_event(sid="ses_other")],
                 on_question=lambda qid, qs: seen.append(qid))
    assert seen == []


def test_sse_question_callback_does_not_block_pump():
    """提问回调要等真人（可能几分钟），期间 delta/tool 事件不能停。"""
    import json
    import threading
    t = OpenCodeTransport()
    t._server_url = "http://127.0.0.1:9999"
    t._session_id = "ses_x"
    release = threading.Event()
    deltas = []
    delta_line = "data: " + json.dumps({
        "type": "message.part.delta",
        "properties": {"field": "text", "delta": "after"},
    })
    _pump_events(t, [_asked_event(), delta_line],
                 on_question=lambda qid, qs: release.wait(10),
                 on_delta=deltas.append)
    release.set()
    assert deltas == ["after"], "提问回调阻塞了事件流"


def test_normalize_question_prefixes_options_for_tui():
    """TUI 靠 'A.' 前缀切选项标号；不加前缀用户就无法选中。"""
    q = {"question": "选哪个?", "header": "业务",
         "options": [{"label": "通用电商平台", "description": "多品类"},
                     {"label": "MVP", "description": ""}]}
    nq, mapping = OpenCodeAgent._normalize_question(q)
    assert nq["question"] == "选哪个?"
    assert nq["options"][0].startswith("A.")
    assert nq["options"][1].startswith("B.")
    assert "通用电商平台" in nq["options"][0]
    assert "多品类" in nq["options"][0]
    assert mapping[nq["options"][0]] == "通用电商平台"
    assert mapping[nq["options"][1]] == "MVP"
    for shown in nq["options"]:
        label = shown.split(".")[0].strip()
        assert label in ("A", "B"), f"TUI 会把 {label!r} 当选项标号"


def test_normalize_question_falls_back_to_header():
    nq, _ = OpenCodeAgent._normalize_question({"header": "业务目标", "options": []})
    assert nq["question"] == "业务目标"
    assert nq["options"] == []


def test_answer_payload_restores_opencode_labels():
    """UI 回的是展示文本，POST 回去必须是 opencode 原始 label。"""
    q = {"question": "?", "options": [{"label": "通用电商平台", "description": "多品类"},
                                      {"label": "最小可行商城", "description": "MVP"}]}
    nq, mapping = OpenCodeAgent._normalize_question(q)
    payload = OpenCodeAgent._to_answer_payload([nq["options"][1]], [mapping])
    assert payload == [["最小可行商城"]]


def test_answer_payload_passes_through_free_text():
    q = {"question": "?", "options": [{"label": "A方案", "description": "d"}]}
    _, mapping = OpenCodeAgent._normalize_question(q)
    assert OpenCodeAgent._to_answer_payload(["自己写的答案"], [mapping]) \
        == [["自己写的答案"]]


def test_question_answer_posted_to_transport(dummy_task):
    """拿到用户回答后必须 POST /question/{id}/reply，否则 agent 继续挂着。"""
    agent, _ = _agent(dummy_task)
    agent.callbacks["on_ask_user"] = lambda qs, rq: rq.put(["B. 最小可行商城 - MVP"])
    with patch.object(agent._transport, "answer_question",
                      return_value=True) as reply, \
         patch.object(agent._transport, "reject_question") as reject:
        agent._on_question_asked("que_1", [{
            "question": "?", "header": "h",
            "options": [{"label": "通用电商平台", "description": "多品类"},
                        {"label": "最小可行商城", "description": "MVP"}]}])
    reply.assert_called_once_with("que_1", [["最小可行商城"]])
    reject.assert_not_called()


def test_question_without_ask_user_support_is_rejected(dummy_task):
    """无 ask_user 通道时必须 reject，绝不能静默挂起。"""
    agent, _ = _agent(dummy_task)
    agent.callbacks.pop("on_ask_user", None)
    with patch.object(agent._transport, "reject_question") as reject:
        agent._on_question_asked("que_1", [{"question": "?", "options": []}])
    reject.assert_called_once_with("que_1")


def test_question_timeout_rejects_instead_of_hanging(dummy_task):
    """用户迟迟不答，也要主动 reject 让 agent 自行决定。"""
    agent, _ = _agent(dummy_task)
    agent.QUESTION_TIMEOUT = 0.05
    agent.callbacks["on_ask_user"] = lambda qs, rq: None  # 永不回答
    with patch.object(agent._transport, "reject_question") as reject, \
         patch.object(agent._transport, "answer_question") as reply:
        agent._on_question_asked("que_1", [{"question": "?", "options": []}])
    reject.assert_called_once_with("que_1")
    reply.assert_not_called()


def test_send_subscribes_question_events(dummy_task):
    """send() 必须把 on_question 接上事件流，否则提问永远无人应答。"""
    agent, _ = _agent(dummy_task)
    agent.running = True
    agent._transport._server_url = "http://127.0.0.1:9999"
    with patch.object(agent._transport, "start_events") as se, \
         patch.object(agent._transport, "send_message",
                      return_value=AgentMessage(text_parts=["ok"])):
        agent.send("hi")
    assert se.call_args.kwargs.get("on_question") == agent._on_question_asked


def test_sse_stream_forced_to_utf8():
    """/event 不带 charset，requests 会退回 ISO-8859-1 把中文解成乱码。"""
    t = OpenCodeTransport()
    t._server_url = "http://127.0.0.1:9999"
    t._session_id = "ses_x"
    resp = _pump_events(t, [], on_delta=lambda _: None)
    assert resp.encoding == "utf-8"
