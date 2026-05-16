import pytest
from sw_lib.ui.tui import MonitorTUI

def test_quit(dummy_task):
    tui = MonitorTUI(dummy_task, "01-brainstorming", 0, "")
    tui._dispatch("/q")
    assert tui.running is False

def test_status_command(dummy_task):
    tui = MonitorTUI(dummy_task, "01-brainstorming", 0, "")
    tui._dispatch("/status")
    sw_logs = [msg for src, msg in tui.state.log_lines if src == "sw"]
    assert any("stage=" in m for m in sw_logs)

def test_non_command_input_routes_advance(dummy_task):
    """非 / 开头的输入应记录为用户日志并发送给 engine"""
    tui = MonitorTUI(dummy_task, "01-brainstorming", 0, "")
    tui._dispatch("hello world")
    user_logs = [msg for src, msg in tui.state.log_lines if src == "user"]
    assert any("hello world" in m for m in user_logs)
