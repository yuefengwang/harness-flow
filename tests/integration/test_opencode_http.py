"""Integration test: OpenCodeAgent lifecycle via public API."""
import os, subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_HAS_OPENCODE = subprocess.run(["which", "opencode"], capture_output=True).returncode == 0


def _make_callbacks():
    logs = []
    return {"add_log": lambda s, m: logs.append((s, m)), "is_running": lambda: True}, logs


def test_start_and_shutdown():
    """start() brings up server, shutdown() tears it down cleanly."""
    if not _HAS_OPENCODE: pytest.skip("opencode not found")
    from sw_lib.agents.opencode import OpenCodeAgent
    a = OpenCodeAgent(_make_callbacks()[0], "test", "01-brainstorming", 0)
    a.start()
    assert a._server_url is not None
    assert a.running
    a.shutdown()
    assert a._server_url is None
    assert not a.running


def test_shutdown_idempotent():
    """Multiple shutdown calls don't crash."""
    if not _HAS_OPENCODE: pytest.skip("opencode not found")
    from sw_lib.agents.opencode import OpenCodeAgent
    a = OpenCodeAgent(_make_callbacks()[0], "test", "01-brainstorming", 0)
    a.start()
    a.shutdown()
    a.shutdown()
    assert a._server_url is None


def test_send_before_start_noop():
    """send() before start() doesn't crash."""
    from sw_lib.agents.opencode import OpenCodeAgent
    a = OpenCodeAgent(_make_callbacks()[0], "test", "01-brainstorming", 0)
    a.send("hello")
    assert a.status != "error"


def test_send_without_server_sets_error():
    """send() without running server sets error status."""
    if not _HAS_OPENCODE: pytest.skip("opencode not found")
    from sw_lib.agents.opencode import OpenCodeAgent
    a = OpenCodeAgent(_make_callbacks()[0], "test", "01-brainstorming", 0)
    a.running = True  # simulate started but no server
    a.send("hello")
    assert a.status == "error"


def test_restart_cycle():
    """restart() creates fresh server."""
    if not _HAS_OPENCODE: pytest.skip("opencode not found")
    from sw_lib.agents.opencode import OpenCodeAgent
    a = OpenCodeAgent(_make_callbacks()[0], "test", "01-brainstorming", 0)
    a.start()
    url1 = a._server_url
    a.restart()
    url2 = a._server_url
    assert url1 != url2  # new port each time
    assert a.running
    a.shutdown()


def test_server_responds_to_health():
    """Started server responds to health check."""
    if not _HAS_OPENCODE: pytest.skip("opencode not found")
    from sw_lib.agents.opencode import OpenCodeAgent
    from urllib.request import Request, urlopen
    a = OpenCodeAgent(_make_callbacks()[0], "test", "01-brainstorming", 0)
    a.start()
    try:
        with urlopen(Request(f"{a._server_url}/global/health"), timeout=5) as r:
            assert r.status == 200
    finally:
        a.shutdown()
