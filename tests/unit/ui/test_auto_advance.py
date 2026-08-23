"""自动推进模式（config.auto_advance / --auto）。

关注点：触发条件是否足够收紧、失败是否回退手动、上限是否兜底。
自动推进一旦在错误时机触发或无限空转，会连续消耗真实模型额度，
因此这里的断言偏保守。
"""
import json
import shutil

import pytest

from sw_lib.core.config import (
    AUTO_ADVANCE_MAX_STAGES, TASKS,
    is_auto_advance, set_auto_advance,
    is_auto_answer, set_auto_answer,
)
from sw_lib.ui.tui import MonitorTUI, _normalize_route_label


@pytest.fixture(autouse=True)
def restore_auto_flag():
    advance, answer = is_auto_advance(), is_auto_answer()
    yield
    set_auto_advance(advance)
    set_auto_answer(answer)


@pytest.fixture
def task():
    name = "auto-advance-test"
    d = TASKS / name
    d.mkdir(parents=True, exist_ok=True)
    (d / ".state").write_text(json.dumps({
        "id": name, "stage": "01-brainstorming", "stage_idx": 0,
        "stage_status": "idle",
    }), encoding="utf-8")
    yield name
    shutil.rmtree(d, ignore_errors=True)


class _StubTUI:
    """只装配 _maybe_auto_advance 需要的状态，不启动 Rich。"""

    def __init__(self, name, auto=True, status="idle"):
        self.state = type("S", (), {
            "name": name, "stage": "01-brainstorming", "stage_idx": 0,
            "agent_status": status, "pending_questions": [],
            "is_settled": False,
        })()
        self.running = True
        self._auto_advance = auto
        self._auto_count = 0
        self._auto_stopped = False
        self.logs = []
        self.advance_calls = []
        self._advance_result = "advanced"

    def _add_log(self, src, msg):
        self.logs.append((src, msg))

    def _run_advance(self, auto=False):
        self.advance_calls.append(auto)
        return self._advance_result


def _run(tui):
    MonitorTUI._maybe_auto_advance(tui)
    return tui


# ── 配置开关 ──

def test_set_auto_advance_roundtrip():
    set_auto_advance(True)
    assert is_auto_advance() is True
    set_auto_advance(False)
    assert is_auto_advance() is False


def test_manual_mode_never_advances(task):
    tui = _run(_StubTUI(task, auto=False))
    assert tui.advance_calls == []


# ── 触发条件 ──

def test_auto_advance_triggers_when_idle(task):
    tui = _run(_StubTUI(task))
    assert tui.advance_calls == [True], "自动模式应以 auto=True 调用推进"


@pytest.mark.parametrize("status", ["active", "connecting", "waiting", "error"])
def test_no_advance_unless_agent_idle(task, status):
    tui = _run(_StubTUI(task, status=status))
    assert tui.advance_calls == [], f"agent_status={status} 时不应自动推进"


def test_no_advance_while_question_pending(task):
    tui = _StubTUI(task)
    tui.state.pending_questions = [{"question": "选哪个?"}]
    assert _run(tui).advance_calls == [], "有待回答提问时不应自动推进"


def test_no_advance_after_settled(task):
    tui = _StubTUI(task)
    tui.state.is_settled = True
    assert _run(tui).advance_calls == []


def test_no_advance_when_stage_pending(task):
    """stage_status=pending 表示 agent 还没跑过，推进只会被门禁拒绝。"""
    d = TASKS / task
    st = json.loads((d / ".state").read_text(encoding="utf-8"))
    st["stage_status"] = "pending"
    (d / ".state").write_text(json.dumps(st), encoding="utf-8")
    assert _run(_StubTUI(task)).advance_calls == []


# ── 失败回退与上限 ──

@pytest.mark.parametrize("result", ["blocked", "error"])
def test_failure_falls_back_to_manual(task, result):
    tui = _StubTUI(task)
    tui._advance_result = result
    _run(tui)
    assert tui._auto_stopped is True, f"result={result} 后应停止自动推进"

    _run(tui)
    assert len(tui.advance_calls) == 1, "回退手动后不应再自动重试"


def test_auto_advance_respects_max_stages(task):
    tui = _StubTUI(task)
    for _ in range(AUTO_ADVANCE_MAX_STAGES + 3):
        _run(tui)
    assert len(tui.advance_calls) == AUTO_ADVANCE_MAX_STAGES
    assert tui._auto_stopped is True
    assert any("上限" in m for _, m in tui.logs)


# ── Route 归一化（自动判定 04-review 路由时用）──

@pytest.mark.parametrize("raw,expected", [
    ("05-archive", "05-Archive"),
    ("03-coding", "03-Coding"),
    ("02-planning", "02-Planning"),
    ("01-brainstorming", "01-Brainstorming"),
])
def test_route_label_matches_hook_expectation(raw, expected):
    """check_04-review.sh 只认 05-Archive 这种大小写，解析结果必须归一化。"""
    assert _normalize_route_label(raw) == expected


def test_route_label_passes_through_unknown():
    assert _normalize_route_label("weird") == "weird"


# ── ask_user 与 auto_advance 必须正交 ──
#
# auto_advance 只管阶段边界（stage 结束、下一个未开始时是否免去 /advance）；
# ask_user 是阶段内的对话轮次。开了自动推进不等于放弃对提问的决策权，
# 代答只由独立的 auto_answer（--unattended）控制。

def test_default_answer_picks_first_option():
    q = {"question": "选哪个?", "options": ["A. 方案甲", "B. 方案乙"]}
    assert MonitorTUI._default_answer(q) == "A. 方案甲"


def test_default_answer_without_options_is_non_empty():
    """无选项时不能回空串 —— agent 会失去判断依据并可能再次提问。"""
    assert MonitorTUI._default_answer({"question": "还有什么补充?"}).strip()


class _AskStub(_StubTUI):
    """补齐 _on_ask_user 所需的状态字段。"""

    def __init__(self, name, auto=True, auto_answer=False):
        super().__init__(name, auto=auto)
        self._auto_answer = auto_answer
        self.state.current_q_idx = 0
        self.state.options = []
        self.state.input_mode = "none"
        self._q_answers = []
        self._q_res_queue = None

    def _update_agent_status(self):
        pass

    _default_answer = staticmethod(MonitorTUI._default_answer)
    # 代答会留下拍板记录（见 test_choice_decision_state.py）。这里转发到真实
    # 实现而不是空实现：桩掉它就等于把「代答是否留痕」这个行为从测试里挖掉。
    _record_decision_direct = MonitorTUI._record_decision_direct


def _ask(tui, questions=None):
    import queue as _q
    res = _q.Queue()
    MonitorTUI._on_ask_user(
        tui, questions or [{"question": "业务模式?", "options": ["A. B2C", "B. B2B"]}], res)
    return res


def test_auto_advance_alone_does_not_answer_questions(task):
    """核心断言：只开自动推进时，提问仍必须交还用户。

    回归背景：曾把代答挂在 _auto_advance 上，导致开了自动推进就等于
    放弃了对 agent 提问的决策权 —— 这两件事是正交的。
    """
    tui = _AskStub(task, auto=True, auto_answer=False)
    res = _ask(tui)
    assert res.empty(), "auto_advance 不应触发代答"
    assert tui.state.pending_questions, "提问应挂起等用户回答"


def test_manual_mode_waits_for_user(task):
    tui = _AskStub(task, auto=False, auto_answer=False)
    assert _ask(tui).empty()
    assert tui.state.pending_questions


def test_auto_answer_alone_answers_without_auto_advance(task):
    """反向正交：只开 auto_answer 时代答生效，且不要求开自动推进。"""
    tui = _AskStub(task, auto=False, auto_answer=True)
    res = _ask(tui)
    assert res.get_nowait() == ["A. B2C"]
    assert tui.state.pending_questions == []


def test_unattended_mode_answers_questions(task):
    tui = _AskStub(task, auto=True, auto_answer=True)
    assert _ask(tui).get_nowait() == ["A. B2C"]


def test_auto_answer_unaffected_by_advance_fallback(task):
    """自动推进已回退手动，不应连带关掉代答（两者独立）。"""
    tui = _AskStub(task, auto=True, auto_answer=True)
    tui._auto_stopped = True
    assert _ask(tui).get_nowait() == ["A. B2C"]


def test_config_switches_are_independent():
    set_auto_advance(True)
    set_auto_answer(False)
    assert is_auto_advance() is True and is_auto_answer() is False

    set_auto_advance(False)
    set_auto_answer(True)
    assert is_auto_advance() is False and is_auto_answer() is True
