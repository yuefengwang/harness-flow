"""面板状态必须成对生效 —— (input_mode, options) 不能出现「模式是 options 但
选项为空」的中间态。

现场 bug（e2e 约 1/3 概率失败）：`_update_agent_status` 早先是先
`self.state.options = extract_options(...)` 清空，再逐个分支做磁盘 IO
（读 .state 判门禁 / 路由）后重填。渲染轮询在主线程，输入在另一个线程 ——
那段 IO 窗口里 `input_mode` 还是上一轮的 "options" 而 `options` 已经空了，
用户此刻按 A 会被 `_validate_input` 判成「请选择有效的选项: 」并**静默丢弃**
（被拒的输入只写 error_msg，不进 .log）。表现是按键像没发出去，日志毫无痕迹。

签署即推进放大了这条：按 A 是每个阶段的必经操作，丢一次就整轮卡住。
"""
import queue
import shutil
import threading
import time
from unittest.mock import patch

import pytest

from sw_lib.core.config import STAGES, TASKS, TPLS
from sw_lib.core.state import write_state
from sw_lib.ui.tui import MonitorTUI, TUIState

_TASK = "pytest-panel-atomic"


@pytest.fixture
def task():
    d = TASKS / _TASK
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    write_state(_TASK, {"id": _TASK, "stage": "03-coding",
                        "stage_idx": STAGES.index("03-coding"),
                        "stage_status": "running"})
    tpl = (TPLS / "03-coding.md").read_text(encoding="utf-8")
    (d / "03-coding.md").write_text(tpl.replace("___", "已填写"), encoding="utf-8")
    yield _TASK
    shutil.rmtree(d, ignore_errors=True)


class _TUI:
    """只替 I/O 的 MonitorTUI —— 面板计算与输入校验走真实实现。"""

    def __init__(self):
        self.state = TUIState(name=_TASK, stage="03-coding",
                              stage_idx=STAGES.index("03-coding"))
        self.state.agent_status = "idle"
        self.state.log_lines = [("agent", "编码完成")]
        self.cmd_queue = queue.Queue()
        self.callbacks = {}
        self.running = True
        self.model_name = "mock"
        self.logs = []
        self._prompt_logged = ""

    def _add_log(self, src, msg):
        self.logs.append((src, msg))

    def _refresh_display(self):
        pass

    def __getattr__(self, name):
        attr = getattr(MonitorTUI, name)
        return attr.__get__(self, type(self)) if callable(attr) else attr


def _refresh(tui):
    """跑一次真实的面板重算（agent 已收尾 → 没有活跃 stage）。"""
    with patch("sw_lib.ui.tui.WorkflowRuntime.get_executor", return_value=None):
        MonitorTUI._update_agent_status(tui)


def test_options_mode_always_has_options(task):
    tui = _TUI()
    _refresh(tui)

    assert tui.state.input_mode == "options"
    assert tui.state.options, "模式是 options 却没有选项 —— 按键会被判无效"


def test_no_empty_options_window_during_refresh(task):
    """重算过程中任何一刻都不该出现 (options, []) 组合。

    用真实的并发去采样：一边持续重算，一边高频读 (input_mode, options)。
    早先的实现里 options 被清空后要等两次磁盘 IO 才重填，这个窗口能被稳定抓到。
    """
    tui = _TUI()
    _refresh(tui)          # 先进入 options 态，制造「上一轮遗留」的前提

    bad = []
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            mode, opts = tui.state.input_mode, tui.state.options
            if mode == "options" and not opts:
                bad.append((mode, list(opts)))
            time.sleep(0)

    t = threading.Thread(target=sample, daemon=True)
    t.start()
    try:
        for _ in range(300):
            _refresh(tui)
    finally:
        stop.set()
        t.join(timeout=5)

    assert not bad, f"重算过程中出现 {len(bad)} 次空选项窗口: {bad[:3]}"


def test_keypress_accepted_across_refreshes(task):
    """并发重算时按 A 必须始终被接受，不能被静默丢弃。"""
    tui = _TUI()
    _refresh(tui)

    rejected = []
    stop = threading.Event()

    def refresher():
        while not stop.is_set():
            _refresh(tui)

    t = threading.Thread(target=refresher, daemon=True)
    t.start()
    try:
        for _ in range(400):
            ok, msg = MonitorTUI._validate_input(tui, "A")
            if not ok:
                rejected.append(msg)
    finally:
        stop.set()
        t.join(timeout=5)

    assert not rejected, \
        f"{len(rejected)}/400 次按 A 被拒（首条: {rejected[0]!r}）"
