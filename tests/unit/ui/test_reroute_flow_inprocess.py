"""04↔03 返工链路的进程内回归（任务 T3）。

覆盖的是 tests/e2e-flow/driver.py 里最容易出错、也最贵的那一段：用户在
04-review 选路由 → 返工到 03 → 再回到 04 → 改选归档。driver 走的是真实
子进程 + PTY，一轮 40 秒，而且要靠"日志静默 3 秒"猜 agent 说完没说完 ——
定位一次问题得跑好几轮。

这里直接驱动同一批生产方法（MonitorTUI._dispatch / _run_advance），不起
子进程、不用 PTY、不 sleep。断言全部落在 `.state` 上，因为判定依据只存那里
（docs/design-json-state-source.md）。

对 driver 的取舍：driver 仍然有价值 —— 它验证真实 PTY 下的按键、Rich 渲染
和输入校验，那是这里模拟不了的。但"流程逻辑对不对"应该在这一层就问出答案。
"""
import queue
import shutil
from unittest.mock import patch

import pytest

from sw_lib.core.config import STAGES, TASKS
from sw_lib.core.state import read_state, write_state
from sw_lib.ui.tui import MonitorTUI, TUIState
from sw_lib.workflow import stage_state as ss

_TASK = "pytest-reroute-flow"


class _StubExecutor:
    """接住 _run_advance 末尾"启动下一阶段 agent"的调用。

    真去 invoke 会拉起 MockAgent 线程并 sleep 若干秒 —— 那正是 driver 慢的
    原因。这里只记录被要求启动的阶段，流程判定不受影响。
    """

    def __init__(self):
        self.active_stage = None
        self.invoked = []
        self.answers = []

    def invoke(self, stage_input):
        self.invoked.append(stage_input.stage)

    def answer(self, text):
        """按键没被任何面板分支认领时，_dispatch 会把它当成给 agent 的回复。

        必须实现：缺了它测试会以 AttributeError 收场，而那条报错完全盖住了
        真正的断言 —— 看起来像替身坏了，其实是"过期 Route 让面板消失，用户
        按的 A 无人认领"。
        """
        self.answers.append(text)

    def handle_command(self, cmd):
        pass


@pytest.fixture
def task(tmp_path):
    """停在 04-review 的任务，target_dir 下有真实产出（否则硬校验会拦）。

    「真实产出」含**可跑的测试**：A6 的客观轨 O3 会把 collected=0 判为
    硬失败（改造前那种项目能通过测试门禁，那正是 O3 要拦的）。
    夹具少了测试文件，返工流程会卡在 blocked 而与被测的路由逻辑无关。
    """
    d = TASKS / _TASK
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)

    target = tmp_path / "repo-app"
    target.mkdir()
    (target / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (target / "README.md").write_text("# app\n\n用法。\n", encoding="utf-8")
    (target / "test_app.py").write_text(
        "from app import f\n\n\ndef test_f():\n    assert f() == 1\n",
        encoding="utf-8")

    write_state(_TASK, {
        "id": _TASK, "stage": "04-review",
        "stage_idx": STAGES.index("04-review"),
        "stage_status": "running",
        "target_dir": str(target),
    })
    for stage in STAGES:
        (d / f"{stage}.md").write_text(
            f"# {stage}\n\n## Security\n- 无敏感数据\n\n"
            "### Reroute Evidence (仅在返工时填写)\n"
            "| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |\n"
            "|---|------|---------|---------|-------------|\n"
            "| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n"
            "| 2 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n\n"
            "## 🤖 AI Output\n\n评审结论。\n",
            encoding="utf-8")
        ss.render_gate_section(_TASK, stage)
    yield d
    shutil.rmtree(d, ignore_errors=True)


class _FakeTUI:
    """只替掉 I/O 的 MonitorTUI。

    渲染、PTY、agent 启动这三样换成空操作，其余一律走真实实现 —— 手搓替身
    最大的风险是落后于生产代码，所以业务方法一个都不重写。
    """

    def __init__(self, stage="04-review"):
        self.state = TUIState(name=_TASK, stage=stage,
                              stage_idx=STAGES.index(stage))
        self.state.agent_status = "idle"
        self.state.log_lines = [("agent", "评审完成")]
        self.cmd_queue = queue.Queue()
        self.callbacks = {}
        self.logs = []
        self._auto_answer = False
        self._auto_advance = False
        self._auto_stopped = False
        self._auto_count = 0
        self._q_answers = []
        self._q_res_queue = None
        # 「等待用户拍板」标记的去重键。真实 __init__ 会建它，这里的替身没跑
        # 真实构造，不显式给就会被 __getattr__ 转发到类上而 AttributeError。
        self._prompt_logged = ""

    # ── 只替 I/O ──
    def _add_log(self, src, msg):
        self.logs.append((src, msg))

    def _refresh_display(self):
        pass

    def _finalize_active_agent(self, active):
        """不去 join 真实 agent 线程：本测试不启动 agent。"""

    def __getattr__(self, name):
        """其余属性/方法全部转发到真实的 MonitorTUI 实现。"""
        attr = getattr(MonitorTUI, name)
        if callable(attr):
            return attr.__get__(self, type(self))
        return attr

    # ── 便于阅读的动作封装 ──
    def press(self, key):
        """模拟用户在面板里按键：先同步渲染态，再走真实分发。

        _update_agent_status 是渲染层决定"显示哪组选项"的地方，_dispatch 又
        依赖同一组判定 —— 必须都走真实实现，否则测的就不是用户看到的那个面板
        （任务 oooo 的 bug 正是渲染与分发判定不一致）。
        """
        MonitorTUI._update_agent_status(self)
        MonitorTUI._dispatch(self, key)

    def sign_off(self):
        """在签署面板按 A —— 这一下同时完成签署与推进。

        返回内部那次推进的结果（"advanced"/"blocked"/...）：签署即推进之后，
        没有"签完再单独 advance"这一步可供断言了。
        """
        self._advance_result = None
        MonitorTUI._update_agent_status(self)
        MonitorTUI._dispatch(self, "A")
        return self._advance_result

    def advance(self, auto=False):
        """显式敲 /advance。签署被拦住后补完内容重试走的是这条路。

        同时充当 _dispatch 内部那次推进的入口 —— __getattr__ 会把生产代码对
        self._run_advance 的调用转发到这里，于是 sign_off 能拿到结果。
        """
        self._advance_result = MonitorTUI._run_advance(self, auto=auto)
        return self._advance_result

    _run_advance = advance

    def messages(self):
        return [m for _, m in self.logs]


@pytest.fixture
def tui(task):
    """接好 executor 替身的 TUI —— 推进不会真起 agent。"""
    stub = _StubExecutor()
    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor",
               return_value=stub):
        t = _FakeTUI()
        t.executor_stub = stub
        yield t


def _stage(name=_TASK):
    return read_state(name)["stage"]


def _enter_stage(tui, stage):
    """把任务和 TUI 都置于 stage 的运行态（模拟 agent 已产出、等待用户）。"""
    st = read_state(_TASK)
    st["stage"] = stage
    st["stage_idx"] = STAGES.index(stage)
    st["stage_status"] = "running"
    write_state(_TASK, st)
    tui.state.stage = stage
    tui.state.stage_idx = STAGES.index(stage)
    tui.state.agent_status = "idle"
    tui.state.log_lines = [("agent", f"{stage} 完成")]


# ── 用户视角：面板给的选项 ──

def test_routing_panel_offered_when_route_undecided(tui):
    """进入 04-review 且未决时，面板必须给 A/B/C 三个路由选项。"""
    MonitorTUI._update_agent_status(tui)

    assert tui.state.input_mode == "options"
    labels = [l for l, _ in tui.state.options]
    assert labels == ["A", "B", "C"], tui.state.options


def test_signoff_panel_offered_after_route_decided(tui):
    """路由定了之后，面板要切成 Gate 签署 —— 而不是继续把 A 当路由。"""
    tui.press("A")                      # A = 05-Archive
    MonitorTUI._update_agent_status(tui)

    assert ss.read_route(_TASK) == "05-archive"
    labels = [l for l, _ in tui.state.options]
    assert labels == ["A", "B"], tui.state.options
    assert "批准" in tui.state.options[0][1]


# ── 完整返工闭环 ──

def test_full_reroute_cycle_reaches_archive(tui):
    """04 →(返工) 03 →(回到) 04 →(改选归档) 05：必须真的到得了归档。

    这就是 T3 的现场：第一轮选返工，第二轮起用户连续三次选归档，流程却每次
    都把他推回 03-coding，在 03↔04 之间循环了六轮。
    """
    # 第一轮：返工到 03-coding
    tui.press("B")
    assert ss.read_route(_TASK) == "03-coding"
    assert tui.sign_off() == "advanced"  # 签 04 的 Gate —— 签署即推进
    assert _stage() == "03-coding"

    # 03 干完活回到 04
    _enter_stage(tui, "03-coding")
    assert tui.sign_off() == "advanced"
    assert _stage() == "04-review"

    # 第二轮：面板必须重新出现，且改选归档要生效
    _enter_stage(tui, "04-review")
    MonitorTUI._update_agent_status(tui)
    assert [l for l, _ in tui.state.options] == ["A", "B", "C"], \
        "第二轮没有重新给出路由面板，用户无从改选"

    tui.press("A")                      # 改选归档
    assert ss.read_route(_TASK) == "05-archive"
    assert tui.sign_off() == "advanced"
    assert _stage() == "05-archive", "第二轮选归档却没去归档 —— 死循环未修"


def test_repeated_archive_choice_is_not_pushed_back(tui):
    """连续两轮都选归档，第二轮不能被上一轮的 target 弹回 03。"""
    tui.press("B")                      # 先返工一次，制造出"上一轮的 Route"
    tui.sign_off()
    _enter_stage(tui, "03-coding")
    tui.sign_off()
    assert _stage() == "04-review"

    # 用户什么都不选就直接推进：过期 Route 不能再生效
    _enter_stage(tui, "04-review")
    tui.press("A")                      # 这一按是路由（面板重新出现）
    assert tui.sign_off() == "advanced"  # 这一按是签署，同时推进
    assert _stage() == "05-archive"


def test_reroute_injects_real_reason_into_coding(tui):
    """返工上下文里要有用户给的真实理由，agent 才知道要补什么。

    T3 里注入的是写死的"需返工修复的问题 / 详见审查结论"，agent 连续三轮
    都没补上用户要的 README.md。
    """
    ss.record_decision(_TASK, "04-review",
                       question="发现缺少 README.md，如何处理？",
                       answer="B. 返工到 03-Coding 补 README.md")

    tui.press("B")
    assert tui.sign_off() == "advanced"

    coding = (TASKS / _TASK / "03-coding.md").read_text(encoding="utf-8")
    assert "返工上下文" in coding, "返工上下文没注入"
    assert "README.md" in coding, "agent 读到的返工理由里没有真实内容"
    assert "详见审查结论" not in coding


def test_gate_is_reset_on_the_target_stage(tui):
    """返工到 03 时，03 上一轮的签名必须失效 —— 否则它会直接放行。"""
    ss.sign_gate(_TASK, "03-coding")
    assert ss.read_gate(_TASK, "03-coding").signed

    tui.press("B")
    tui.sign_off()

    assert not ss.read_gate(_TASK, "03-coding").signed, \
        "返工目标带着上一轮的签名，推进会无条件放行"


def test_advance_blocked_when_gate_unsigned(tui):
    """没签 Gate 就推进要被拦住，并且留在原阶段。"""
    tui.press("A")                      # 只定路由，不签 Gate

    assert tui.advance() == "blocked"
    assert _stage() == "04-review"


def test_invalid_choice_does_not_write_route(tui):
    """无效按键不能写出一个路由决策。"""
    tui.press("Z")

    assert ss.read_route(_TASK) is None
    assert any("无效选项" in m for m in tui.messages()), tui.messages()
