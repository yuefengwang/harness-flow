"""签署即推进 —— 按 [A] 批准之后不该再要求用户敲一次 /advance。

签 Gate 表达的就是「可以走了」，让用户先批准、再手动推进是把一个决定拆成
两步。这里锁住新行为，并重点验证它与 **auto_advance** 的兼容：两条路径都
会调 `_run_advance`，重叠处必须清楚。

职责划分（不能混）：

* auto_advance 开启时用户看不到签署面板 —— `_maybe_auto_advance` 在渲染轮询里
  先触发，`_auto_sign_off` 代签，走的是 auto=True。
* 手动签署走 auto=False，**不消耗** 自动推进预算（`_auto_count`），也不该因
  为自动推进早先回退过手动（`_auto_stopped`）而被拒。
* 两者都在主循环同一线程里：`_dispatch` 在 `cmd_queue.get()` 成功分支，
  `_maybe_auto_advance` 在超时分支，互斥，不会并发推进同一阶段。
"""
import json
import queue
import shutil
from unittest.mock import patch

import pytest

from sw_lib.core.config import AUTO_ADVANCE_MAX_STAGES, STAGES, TASKS, TPLS
from sw_lib.core.state import write_state
from sw_lib.ui.tui import MonitorTUI, TUIState
from sw_lib.workflow import stage_state as ss

_TASK = "pytest-signoff-advances"


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
    """只替 I/O 与推进的 MonitorTUI —— 签署判定走真实实现。

    `_run_advance` 记录调用参数而不真跑校验：本模块关心的是「签署有没有触发
    推进、以什么模式触发」，校验逻辑本身由 test_reroute_flow_inprocess 覆盖。
    """

    def __init__(self, stage="03-coding", auto=False):
        self.state = TUIState(name=_TASK, stage=stage,
                              stage_idx=STAGES.index(stage))
        self.state.agent_status = "idle"
        self.state.input_mode = "options"
        self.state.log_lines = [("agent", "编码完成")]
        self.cmd_queue = queue.Queue()
        self.callbacks = {}
        self.running = True
        self.logs = []
        self.advance_calls = []
        self._advance_result = "advanced"
        self._auto_answer = False
        self._auto_advance = auto
        self._auto_count = 0
        self._auto_stopped = False
        self._prompt_logged = ""

    def _add_log(self, src, msg):
        self.logs.append((src, msg))

    def _refresh_display(self):
        pass

    def _run_advance(self, auto=False):
        self.advance_calls.append(auto)
        return self._advance_result

    def __getattr__(self, name):
        attr = getattr(MonitorTUI, name)
        return attr.__get__(self, type(self)) if callable(attr) else attr

    def press(self, key):
        MonitorTUI._dispatch(self, key)


# ── 手动签署：一步到位 ──

def test_signoff_triggers_advance(task):
    tui = _TUI()
    tui.press("A")

    assert tui.advance_calls == [False], \
        "签署后没有推进（或用了 auto 模式）—— 用户还得再敲一次 /advance"
    assert ss.read_gate(_TASK, "03-coding").signed


def test_signoff_message_does_not_ask_for_advance(task):
    """提示语不该再教用户敲 /advance，否则和实际行为矛盾。"""
    tui = _TUI()
    tui.press("A")

    msgs = [m for src, m in tui.logs if src == "sw"]
    assert not any("输入 /advance" in m for m in msgs), msgs


def test_revision_choice_does_not_advance(task):
    """按 [B] 要求修订 → 既不签署也不推进。"""
    tui = _TUI()
    tui.press("B")

    assert tui.advance_calls == []
    assert not ss.read_gate(_TASK, "03-coding").signed


def test_invalid_choice_does_not_advance(task):
    tui = _TUI()
    tui.press("X")

    assert tui.advance_calls == []
    assert not ss.read_gate(_TASK, "03-coding").signed


def test_blocked_advance_keeps_gate_signed(task):
    """校验没过时 Gate 仍是签好的 —— 用户补完内容敲 /advance 即可，不必重新批准。"""
    tui = _TUI()
    tui._advance_result = "blocked"
    tui.press("A")

    assert tui.advance_calls == [False]
    assert ss.read_gate(_TASK, "03-coding").signed, \
        "被拦住就丢掉签名，用户得重新批准一遍"


# ── 与 auto_advance 的兼容 ──

def test_manual_signoff_does_not_consume_auto_budget(task):
    """手动签署推进不该记进自动推进的配额。

    _auto_count 是给无人值守场景兜底的（AUTO_ADVANCE_MAX_STAGES 次后停下），
    把用户的手动决定算进去，会让自动模式提前退出。
    """
    tui = _TUI(auto=True)
    tui.press("A")

    assert tui._auto_count == 0, "手动签署占用了自动推进预算"
    assert tui.advance_calls == [False]


def test_manual_signoff_works_after_auto_fell_back(task):
    """自动推进回退手动后，用户按 A 仍要能推进。

    _auto_stopped 只约束自动路径；它若拦住手动签署，任务就彻底卡死了。
    """
    tui = _TUI(auto=True)
    tui._auto_stopped = True
    tui.press("A")

    assert tui.advance_calls == [False]
    assert ss.read_gate(_TASK, "03-coding").signed


def test_manual_signoff_does_not_disable_auto_advance(task):
    """手动签一次不等于关掉自动推进 —— 后续阶段仍该自动走。"""
    tui = _TUI(auto=True)
    tui.press("A")

    assert tui._auto_advance is True
    assert tui._auto_stopped is False


def test_auto_mode_signs_off_without_user_press(task):
    """auto 模式下用户不必按 A：_maybe_auto_advance 会代签并推进。

    这条和上面几条一起划清边界：auto 走 auto=True（内部代签），手动走
    auto=False（用户已签）。两条路径不能互相顶掉。
    """
    st = json.loads((TASKS / _TASK / ".state").read_text(encoding="utf-8"))
    st["stage_status"] = "running"
    (TASKS / _TASK / ".state").write_text(json.dumps(st), encoding="utf-8")

    tui = _TUI(auto=True)
    MonitorTUI._maybe_auto_advance(tui)

    assert tui.advance_calls == [True], "自动推进未触发或用错了模式"
    assert tui._auto_count == 1


def test_auto_budget_still_capped(task):
    """手动签署穿插其间，也不能让自动配额失效。"""
    tui = _TUI(auto=True)
    for _ in range(AUTO_ADVANCE_MAX_STAGES + 2):
        MonitorTUI._maybe_auto_advance(tui)

    auto_calls = [a for a in tui.advance_calls if a is True]
    assert len(auto_calls) == AUTO_ADVANCE_MAX_STAGES
    assert tui._auto_stopped is True


def test_signoff_panel_hidden_once_gate_signed(task):
    """签署过后面板不该还停在 A/B —— 否则用户会重复批准。"""
    tui = _TUI()
    tui.press("A")

    assert tui.state.input_mode == "none"
    assert tui.state.options == []


# ── 04-review：路由与签署是两个决定，只有后者推进 ──

@pytest.fixture
def review_task():
    d = TASKS / _TASK
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    write_state(_TASK, {"id": _TASK, "stage": "04-review",
                        "stage_idx": STAGES.index("04-review"),
                        "stage_status": "running"})
    tpl = (TPLS / "04-review.md").read_text(encoding="utf-8")
    (d / "04-review.md").write_text(tpl.replace("___", "已填写"), encoding="utf-8")
    yield _TASK
    shutil.rmtree(d, ignore_errors=True)


def test_review_route_choice_does_not_advance(review_task):
    """第一次按 A 是选路由，不是批准 —— 此时推进会跳过用户的批准。"""
    tui = _TUI(stage="04-review")
    tui.press("A")

    assert tui.advance_calls == [], "路由决策阶段就推进了，用户还没批准"
    assert ss.read_route(_TASK) == "05-archive"
    assert not ss.read_gate(_TASK, "04-review").signed


def test_review_signoff_after_route_advances(review_task):
    """Route 落定后面板切成签署选项，那一次 A 才推进。"""
    tui = _TUI(stage="04-review")
    tui.press("A")                      # 路由
    # 面板重算走真实实现（这正是「切成 Gate 签署」的地方）；agent 已收尾，
    # 所以 executor 取不到活跃 stage —— 用 None 如实表达这个状态。
    with patch("sw_lib.ui.tui.WorkflowRuntime.get_executor", return_value=None):
        tui._update_agent_status()
    assert tui.state.input_mode == "options"
    tui.press("A")                      # 签署

    assert tui.advance_calls == [False]
    assert ss.read_gate(_TASK, "04-review").signed
