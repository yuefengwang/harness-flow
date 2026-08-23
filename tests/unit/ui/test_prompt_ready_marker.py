"""「等待用户拍板」标记 —— 自动化驱动唯一可靠的按键时机信号。

e2e 只跑 MockAgent 之后，driver 必须精确知道「此刻按 A 不会被拒」。这个判定
的真实依据是 `MonitorTUI._update_agent_status` 里把 `input_mode` 切成
options 那一刻，所以 TUI 自己把这个状态转换记进 `.log`，driver 读它。

踩过的坑（每条都对应下面一个用例）：

* 猜「日志静默 3 秒」—— MockAgent 脚本自带 sleep(2)，静默窗口可能落在两行
  输出之间，过早按 A 被 `_validate_input` 拒绝。三轮挂一轮。
* 只等「脚本收尾」—— 脚本说完了但 TUI 还没在 40ms 轮询里切 input_mode。
* 只等「面板打开」—— 01 阶段答完提问后有个瞬时 idle 窗口，面板会在 agent
  继续输出前先开一次，此时 agent 随即转回 active，按 A 照样被拒。

所以标记必须**每次面板真正打开时**都记、而且渲染轮询里不能刷屏。
"""
import queue
import shutil
from unittest.mock import patch

import pytest

from sw_lib.core.config import STAGES, TASKS
from sw_lib.core.state import read_state, write_state
from sw_lib.ui.tui import PROMPT_READY_MARKER, MonitorTUI, TUIState
from sw_lib.workflow import stage_state as ss

_TASK = "pytest-prompt-ready"


@pytest.fixture
def task():
    d = TASKS / _TASK
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    write_state(_TASK, {
        "id": _TASK, "stage": "04-review",
        "stage_idx": STAGES.index("04-review"),
        "stage_status": "running",
    })
    for stage in STAGES:
        (d / f"{stage}.md").write_text(
            f"# {stage}\n\n## 🤖 AI Output\n\n产出。\n", encoding="utf-8")
        ss.render_gate_section(_TASK, stage)
    yield d
    shutil.rmtree(d, ignore_errors=True)


class _StubAgent:
    def __init__(self, name: str, status: str):
        self.name = name
        self.status = status


class _StubStage:
    def __init__(self, agent):
        self.active_agent = agent


class _StubExecutor:
    """按需摆出 agent 状态的 executor 替身。

    _update_agent_status 从这里读 agent.status —— 也就是决定「弹不弹拍板面板」
    的输入。真实 bootstrap 会拉起 langgraph 与 agent 线程，那不是本测试的关切。
    """

    def __init__(self, status: str = "idle"):
        self.active_stage = _StubStage(_StubAgent(_TASK, status)) if status else None


class _FakeTUI:
    """只替 I/O 的 MonitorTUI —— 业务判定一律走真实实现。"""

    def __init__(self, stage: str):
        self.state = TUIState(name=_TASK, stage=stage,
                              stage_idx=STAGES.index(stage))
        self.state.agent_status = "idle"
        self.state.log_lines = [("agent", "阶段产出完成")]
        self.cmd_queue = queue.Queue()
        self.callbacks = {}
        self.logs = []
        self.model_name = "mock"
        self._auto_answer = False
        self._prompt_logged = ""
        self.agent_status = "idle"
        # _update_agent_status 会用 .state 里的 stage 覆盖 self.state.stage，
        # 所以磁盘状态必须与夹具想测的阶段一致。
        st = read_state(_TASK)
        st["stage"] = stage
        st["stage_idx"] = STAGES.index(stage)
        write_state(_TASK, st)

    def _add_log(self, src, msg):
        self.logs.append((src, msg))

    def _refresh_display(self):
        pass

    def __getattr__(self, name):
        attr = getattr(MonitorTUI, name)
        return attr.__get__(self, type(self)) if callable(attr) else attr

    def markers(self):
        return [m for _, m in self.logs if PROMPT_READY_MARKER in m]

    def sync(self):
        """跑一次渲染轮询做的状态同步（真实 _update_agent_status）。"""
        with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor",
                   return_value=_StubExecutor(self.agent_status)):
            MonitorTUI._update_agent_status(self)


def test_marker_logged_when_gate_panel_opens(task):
    tui = _FakeTUI("02-planning")
    tui.sync()

    assert tui.state.input_mode == "options", "夹具没让签署面板打开"
    assert len(tui.markers()) == 1


def test_marker_not_repeated_by_render_polling(task):
    """渲染轮询每 40ms 跑一次，标记不能被刷成上千行。"""
    tui = _FakeTUI("02-planning")
    for _ in range(50):
        tui.sync()

    assert len(tui.markers()) == 1


def test_no_marker_while_agent_active(task):
    """agent 还在输出时面板不该开，也就不该有标记 —— 这正是按 A 会被拒的时刻。"""
    tui = _FakeTUI("02-planning")
    tui.agent_status = "active"
    tui.sync()

    assert tui.markers() == []


def test_no_marker_while_questions_pending(task):
    """有待答提问时是提问面板，不是拍板面板。"""
    tui = _FakeTUI("02-planning")
    tui.state.pending_questions = [
        {"question": "选哪个？", "options": ["A. 甲", "B. 乙"]}
    ]
    tui.sync()

    assert tui.markers() == []


def test_marker_logged_again_after_gate_reset(task):
    """返工回到同一阶段时面板会重开，那一轮必须重新记录。

    去重键按 kind:stage 算，如果不在面板关闭时清掉，第二轮的驱动会一直等不到
    信号 —— 表现是 e2e 超时，看起来像流程 bug。
    """
    tui = _FakeTUI("02-planning")
    tui.sync()
    assert len(tui.markers()) == 1

    # 用户签署 → 面板关闭
    ss.sign_gate(_TASK, "02-planning", by="user")
    tui.sync()
    assert tui.state.input_mode != "options"

    # 返工：签名被撤销，面板重开
    ss.reset_gate(_TASK, "02-planning")
    tui.sync()

    assert len(tui.markers()) == 2, "面板重开却没有新标记，驱动会卡死等信号"


def test_route_and_gate_panels_log_separately(task):
    """04-review 先开路由面板、Route 落定后再开签署面板 —— 两条独立标记。

    driver 在这个阶段要按两次 A，靠计数区分先后。
    """
    tui = _FakeTUI("04-review")
    tui.sync()

    first = tui.markers()
    assert len(first) == 1
    assert "路由决策" in first[0]

    assert ss.write_route(_TASK, "05-Archive", by="user")
    tui.sync()

    second = tui.markers()
    assert len(second) == 2, "Route 落定后签署面板的标记没出来"
    assert "门禁签署" in second[1]


def test_marker_names_the_stage(task):
    """标记要带阶段名：驱动失败时靠它判断卡在哪一轮。"""
    tui = _FakeTUI("03-coding")
    tui.sync()

    assert "03-coding" in tui.markers()[0]
