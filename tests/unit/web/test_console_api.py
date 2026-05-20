import pytest
from fastapi.testclient import TestClient
from sw_lib.web.app import create_app
from sw_lib.web.engine_manager import WebEngineManager


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def clean_sessions():
    mgr = WebEngineManager()
    yield mgr
    for name in list(mgr._sessions.keys()):
        mgr.destroy_session(name)


def test_console_page_200(dummy_task, client):
    resp = client.get(f"/tasks/{dummy_task}/console")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert dummy_task in resp.text


def test_console_page_404(client):
    resp = client.get("/tasks/nonexistent/console")
    assert resp.status_code == 404


def test_sse_requires_session(dummy_task, client):
    resp = client.get(f"/tasks/{dummy_task}/sse")
    assert resp.status_code == 404
    assert "会话不存在" in resp.text


def test_engine_start_creates_session(dummy_task, client, clean_sessions):
    resp = client.post(f"/tasks/{dummy_task}/engine/start")
    assert resp.status_code == 200
    session = clean_sessions.get_session(dummy_task)
    assert session is not None


def test_engine_start_double(dummy_task, client, clean_sessions):
    resp1 = client.post(f"/tasks/{dummy_task}/engine/start")
    assert resp1.status_code == 200
    resp2 = client.post(f"/tasks/{dummy_task}/engine/start")
    assert resp2.status_code == 200


def test_engine_answer_no_session(dummy_task, client):
    resp = client.post(
        f"/tasks/{dummy_task}/engine/answer",
        data={"text": "hello"},
    )
    assert resp.status_code == 404


def test_engine_answer_with_session(dummy_task, client, clean_sessions):
    client.post(f"/tasks/{dummy_task}/engine/start")
    resp = client.post(
        f"/tasks/{dummy_task}/engine/answer",
        data={"text": "test reply"},
    )
    assert resp.status_code == 200


def test_engine_command_no_session(dummy_task, client):
    resp = client.post(
        f"/tasks/{dummy_task}/engine/command",
        data={"cmd": "status"},
    )
    assert resp.status_code == 404


def test_engine_command_with_session(dummy_task, client, clean_sessions):
    client.post(f"/tasks/{dummy_task}/engine/start")
    resp = client.post(
        f"/tasks/{dummy_task}/engine/command",
        data={"cmd": "status"},
    )
    assert resp.status_code == 200


def test_sse_route_registered(client):
    sse_routes = [r for r in client.app.routes if hasattr(r, "path") and "/sse" in r.path]
    assert len(sse_routes) == 1


@pytest.mark.asyncio
async def test_event_stream_yields_events(dummy_task):
    from sw_lib.web.engine_manager import WebEngineManager
    mgr = WebEngineManager()
    session = mgr.create_session(dummy_task, "02-planning", 1, "N/A")
    try:
        session._web_add_log("agent", "test msg")
        event = await session.get_event()
        assert event is not None
        assert event["type"] == "log"
        assert event["msg"] == "test msg"
        assert event["source"] == "agent"
    finally:
        mgr.destroy_session(dummy_task)
