"""被输入校验拒绝的文本不能留在缓冲区里。

现场（e2e 偶发 + 任务 T3 的 TUI 表现）：agent 还在输出时按 A，校验拒绝
（"Agent 正在处理中，请稍后输入"），但字符仍留在 input_buffer。用户接着敲
/advance，实际提交的是 "A/advance" —— 没有任何分支认这条命令，屏幕上看起来
就是"TUI 没反应"。

这里调用生产代码里那个真实方法（_submit_input），不另写一份等价逻辑 ——
手搓的替身会随生产代码演进而落后，测试就变成假绿。
"""
import queue

import pytest

from sw_lib.core.config import STAGES
from sw_lib.ui.tui import MonitorTUI, TUIState


def _tui(agent_status="active"):
    tui = MonitorTUI.__new__(MonitorTUI)
    tui.state = TUIState(name="pytest-input-buf", stage="01-brainstorming",
                         stage_idx=STAGES.index("01-brainstorming"))
    tui.state.agent_status = agent_status
    tui.state.input_mode = "none"
    tui.cmd_queue = queue.Queue()
    tui._refresh_display = lambda: None
    return tui


def _type(tui, text):
    """模拟逐字符输入后按回车：填缓冲区，再走真实的提交路径。"""
    for ch in text:
        tui.state.input_buffer += ch
    return MonitorTUI._submit_input(tui, tui.state.input_buffer)


def test_rejected_input_clears_buffer():
    """被拒后缓冲区必须清空，否则会污染下一条命令。"""
    tui = _tui(agent_status="active")

    _type(tui, "A")

    assert tui.state.error_msg, "应当给出拒绝原因"
    assert tui.state.input_buffer == "", \
        f"被拒的输入残留在缓冲区: {tui.state.input_buffer!r}"


def test_next_command_is_not_polluted():
    """完整复现：先按 A 被拒，再输 /advance，提交的必须是干净的 /advance。"""
    tui = _tui(agent_status="active")
    _type(tui, "A")

    tui.state.agent_status = "idle"
    _type(tui, "/advance")

    submitted = []
    while not tui.cmd_queue.empty():
        submitted.append(tui.cmd_queue.get())
    assert submitted == ["/advance"], f"命令被污染: {submitted}"


def test_accepted_input_still_clears_buffer():
    tui = _tui(agent_status="idle")

    _type(tui, "hello")

    assert tui.state.input_buffer == ""
    assert tui.cmd_queue.get() == "hello"


@pytest.mark.parametrize("mode,options,bad", [
    ("options", [("A", "批准"), ("B", "修订")], "Z"),
    ("yesno", [], "maybe"),
])
def test_rejected_choice_clears_buffer(mode, options, bad):
    """选项模式与 yes/no 模式下的无效输入同样不能残留。"""
    tui = _tui(agent_status="idle")
    tui.state.input_mode = mode
    tui.state.options = options

    _type(tui, bad)

    assert tui.state.error_msg
    assert tui.state.input_buffer == ""


def test_slash_commands_bypass_validation():
    """斜杠命令始终放行 —— 否则 agent 忙时连 /q 都退不出去。"""
    tui = _tui(agent_status="active")

    _type(tui, "/q")

    assert tui.cmd_queue.get() == "/q"
    assert tui.state.input_buffer == ""
