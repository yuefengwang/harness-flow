"""Integration test: OpenCodeAgent lifecycle via public API (option A)."""
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
    assert a._transport.server_url is not None
    assert a.running
    a.shutdown()
    assert a._transport.server_url is None
    assert not a.running


def test_shutdown_idempotent():
    """Multiple shutdown calls don't crash."""
    if not _HAS_OPENCODE: pytest.skip("opencode not found")
    from sw_lib.agents.opencode import OpenCodeAgent
    a = OpenCodeAgent(_make_callbacks()[0], "test", "01-brainstorming", 0)
    a.start()
    a.shutdown()
    a.shutdown()
    assert a._transport.server_url is None


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
    """restart() 后仍得到一个可用的 server。

    注意：不再断言"每次端口都不同"。旧实现自己 bind 探测随机空闲端口，
    所以端口必变；现在改由 `--port 0` 交给 opencode 选，它会优先复用 4096。
    端口是否变化属于实现细节，真正要保证的是重启后服务健康可用。
    """
    if not _HAS_OPENCODE: pytest.skip("opencode not found")
    from sw_lib.agents.opencode import OpenCodeAgent
    a = OpenCodeAgent(_make_callbacks()[0], "test", "01-brainstorming", 0)
    a.start()
    assert a._transport.server_url is not None
    a.restart()
    assert a._transport.server_url is not None
    assert a._transport.health()
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
        with urlopen(Request(f"{a._transport.server_url}/global/health"), timeout=5) as r:
            assert r.status == 200
    finally:
        a.shutdown()
