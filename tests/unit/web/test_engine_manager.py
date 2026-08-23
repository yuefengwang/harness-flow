import asyncio
import pytest
from sw_lib.web.engine_manager import WebEngineManager


def test_engine_manager_singleton():
    m1 = WebEngineManager()
    m2 = WebEngineManager.get_instance()
    assert m1 is m2


def test_create_session(dummy_task):
    mgr = WebEngineManager()
    session = mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    assert session is not None
    assert session.is_alive
    assert session.task_name == dummy_task
    assert mgr.get_session(dummy_task) is session
    mgr.destroy_session(dummy_task)


def test_get_session_nonexistent():
    mgr = WebEngineManager()
    assert mgr.get_session("nonexistent-task") is None


def test_destroy_session(dummy_task):
    mgr = WebEngineManager()
    mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    assert mgr.is_alive(dummy_task)
    mgr.destroy_session(dummy_task)
    assert not mgr.is_alive(dummy_task)
    assert mgr.get_session(dummy_task) is None


def test_create_session_destroy_old(dummy_task):
    mgr = WebEngineManager()
    s1 = mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    s2 = mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    assert s2 is not s1
    assert not s1.is_alive
    assert mgr.get_session(dummy_task) is s2
    mgr.destroy_session(dummy_task)


def test_web_add_log_pushes_to_queue(dummy_task):
    mgr = WebEngineManager()
    session = mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        session._web_add_log("agent", "Hello from agent")
        session._web_add_log("sw", "System message")
        session._web_add_log("error", "Something went wrong")
        events = loop.run_until_complete(session.get_all_events())
        assert len(events) == 3
        assert events[0]["type"] == "log"
        assert events[0]["source"] == "agent"
        assert events[0]["msg"] == "Hello from agent"
        assert "ts" in events[0]
        assert events[1]["source"] == "sw"
        assert events[2]["source"] == "error"
    finally:
        loop.close()
        mgr.destroy_session(dummy_task)


@pytest.mark.asyncio
async def test_get_event_timeout(dummy_task):
    mgr = WebEngineManager()
    session = mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(session.get_event(), timeout=0.1)
    finally:
        mgr.destroy_session(dummy_task)


@pytest.mark.asyncio
async def test_get_all_events_empty(dummy_task):
    mgr = WebEngineManager()
    session = mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    try:
        events = await session.get_all_events()
        assert events == []
    finally:
        mgr.destroy_session(dummy_task)


def test_session_status_initially_idle(dummy_task):
    from sw_lib.core.bootstrap import bootstrap
    bootstrap()
    mgr = WebEngineManager()
    session = mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    try:
        assert session.status == "idle"
    finally:
        mgr.destroy_session(dummy_task)


def test_session_submit_answer_no_crash(dummy_task):
    from sw_lib.core.bootstrap import bootstrap
    bootstrap()
    mgr = WebEngineManager()
    session = mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    try:
        session.submit_answer("test reply")
        session.submit_command("status")
    finally:
        mgr.destroy_session(dummy_task)


def test_web_on_settlement(dummy_task):
    mgr = WebEngineManager()
    session = mgr.create_session(dummy_task, "01-brainstorming", 0, "N/A")
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        session._web_on_settlement()
        events = loop.run_until_complete(session.get_all_events())
        assert len(events) == 1
        assert events[0]["type"] == "settlement"
        assert "stage" in events[0]
    finally:
        loop.close()
        mgr.destroy_session(dummy_task)
