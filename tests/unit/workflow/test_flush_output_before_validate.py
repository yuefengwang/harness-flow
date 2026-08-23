"""校验前必须先把 agent 已产出的内容落盘。

e2e 复现（mock 全流程）：多轮模式下 ``_save_stage_output`` 在 ``invoke()`` 里、
``_run_agent()`` 返回之后才执行；而 ``_run_agent`` 要等 ``_stage_done``，
``_stage_done`` 又只在**校验通过后**由 ``_finalize_active_agent`` 设置。
于是形成闭环：校验读到的永远是还没写入产出的文件，任何需要 agent 回填的
模板都无法在第一次 /advance 通过 —— 用户看到「N 个待填项未完成」，指的却是
agent 已经说过、只是还没落盘的内容。

修复方向：校验前先 flush 一次产出快照，且**不**关闭 agent。
"""
import shutil

import pytest

from sw_lib.core.config import TASKS, TPLS


_TASK = "pytest-flush-before-validate"


@pytest.fixture
def stage_file():
    d = TASKS / _TASK
    d.mkdir(parents=True, exist_ok=True)
    tpl = (TPLS / "01-brainstorming.md").read_text(encoding="utf-8")
    (d / "01-brainstorming.md").write_text(tpl, encoding="utf-8")
    yield d / "01-brainstorming.md"
    shutil.rmtree(d, ignore_errors=True)


def _runnable():
    from sw_lib.workflow.base import StageRunnable
    r = StageRunnable.__new__(StageRunnable)
    r.stage = "01-brainstorming"
    r._agent_text_buffer = ["## 结论\n采用方案 B（B2B 批发）。"]
    r._agent_output_lines = []
    return r


def test_flush_writes_agent_output_without_shutdown(stage_file):
    r = _runnable()

    assert r.flush_output(_TASK) is True

    content = stage_file.read_text(encoding="utf-8")
    assert "采用方案 B" in content, "agent 产出未落盘，校验将读到空模板"
    assert "## Gate" in content, "Gate 区被 flush 破坏"


def test_flush_is_idempotent(stage_file):
    """/advance 可能被多次触发，重复 flush 不得产生多个 AI Output 区。"""
    r = _runnable()
    r.flush_output(_TASK)
    r.flush_output(_TASK)

    content = stage_file.read_text(encoding="utf-8")
    assert content.count("## 🤖 AI Output") == 1, "重复 flush 产生了多个产出区"


def test_flush_noop_without_output(stage_file):
    """agent 还没说话时 flush 不该改动文件。"""
    r = _runnable()
    r._agent_text_buffer = []
    before = stage_file.read_text(encoding="utf-8")

    assert r.flush_output(_TASK) is False
    assert stage_file.read_text(encoding="utf-8") == before


def test_advance_flushes_before_validation(stage_file):
    """/advance 必须在软校验之前 flush，否则校验读到的是旧内容。"""
    import queue
    from unittest.mock import MagicMock, patch
    from sw_lib.ui.tui import MonitorTUI, TUIState

    tui = MonitorTUI.__new__(MonitorTUI)
    # 真实 TUIState 而非 MagicMock：mock 未设置的属性是真值，会让
    # _run_advance/_dispatch 的前置分支恒真，把真正要验的逻辑绕过去。
    tui.state = TUIState(name=_TASK, stage="01-brainstorming", stage_idx=0)
    tui.cmd_queue = queue.Queue()
    tui.callbacks = {}
    tui.logs = []
    tui._add_log = lambda src, msg: tui.logs.append((src, msg))
    tui._refresh_display = lambda: None

    order = []
    active = MagicMock()
    active.flush_output.side_effect = lambda name: order.append("flush")
    executor = MagicMock(active_stage=active)

    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor",
               return_value=executor), \
         patch("sw_lib.core.service._service") as svc:
        svc.get_task_state.return_value = {"stage_idx": 0, "stage_status": "running"}
        svc.validate_stage.side_effect = lambda *a, **k: (
            order.append("validate") or ([], ["还有待填项"]))
        tui._run_advance(auto=False)

    assert order[:2] == ["flush", "validate"], \
        f"flush 必须早于校验，实际顺序: {order}"
