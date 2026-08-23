"""Gate 必须有人能签 —— 每个阶段都要有入口。

现场 bug（任务 iiiii）：/advance 在 02/03/04/05 报「N 个待填项未完成」，
但 TUI 里只有 01 提供了勾 Gate 的入口（用户按 [A] 批准设计）。
其余阶段的 Gate 没有任何签署路径：``auto_check_gate`` 是在
``WorkflowRuntime.advance()`` 内部跑的，也就是**校验通过之后**，
所以 /advance 要求的正是它随后才会补上的东西 —— 死锁。
"""
import queue
import shutil
from unittest.mock import patch

import pytest

from sw_lib.core.config import STAGES, TASKS, TPLS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.ui.tui import MonitorTUI, TUIState
from sw_lib.workflow.utils import check_stage_compliance

_TASK = "pytest-gate-signoff"


@pytest.fixture
def task_dir():
    d = TASKS / _TASK
    d.mkdir(parents=True, exist_ok=True)
    # 门禁判定读 .state（docs/design-json-state-source.md），必须有状态文件
    write_state(_TASK, {"id": _TASK, "stage": "01-brainstorming",
                        "stage_idx": 0, "stage_status": "running"})
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _tui(stage, status="idle"):
    tui = MonitorTUI.__new__(MonitorTUI)
    # 用真实的 TUIState 而不是 MagicMock：mock 上未设置的属性是真值
    # (bool(m.pending_questions) is True)，会让 _dispatch 的前置分支恒真、
    # 后面的路由/签署分支永远不可达 —— 测试会"通过"却什么都没验证。
    tui.state = TUIState(name=_TASK, stage=stage,
                         stage_idx=STAGES.index(stage))
    tui.state.agent_status = status
    tui.state.input_mode = "options"
    tui.cmd_queue = queue.Queue()
    tui.callbacks = {}
    tui._auto_answer = False
    tui.logs = []
    tui._add_log = lambda src, msg: tui.logs.append((src, msg))
    tui._refresh_display = lambda: None
    return tui


def _write_agent_output(d, stage):
    """模拟 agent 已完成：正文填好、Gate 仍未签署。

    04-review 额外把 Route 决策落进 .state —— 路由是比签署更前置的决定，
    未决时 TUI 先给路由选项，不会提示签署。
    """
    tpl = (TPLS / f"{stage}.md").read_text(encoding="utf-8")
    content = tpl.replace("___", "已填写内容")
    (d / f"{stage}.md").write_text(content, encoding="utf-8")
    if stage == "04-review":
        ss.write_route(_TASK, "05-Archive")
    return content


@pytest.mark.parametrize("stage", STAGES)
def test_gate_signoff_offered_for_every_stage(task_dir, stage):
    """agent 收尾、Gate 未勾时，每个阶段都应提示用户签署。"""
    _write_agent_output(task_dir, stage)
    assert MonitorTUI._is_gate_signoff_needed(_tui(stage)) is True, \
        f"{stage}: Gate 未勾却没有任何签署入口，/advance 会永久被拒"


@pytest.mark.parametrize("stage", STAGES)
def test_gate_signoff_unblocks_advance(task_dir, stage):
    """签署后软校验必须真的放行 —— 这是「用户能完成」的判定标准。"""
    _write_agent_output(task_dir, stage)
    tui = _tui(stage)

    assert MonitorTUI._write_gate_signoff(tui) is True

    content = (task_dir / f"{stage}.md").read_text(encoding="utf-8")
    gate = content[content.rfind("## Gate"):]
    assert "[ ]" not in gate, f"{stage}: 渲染后的 Gate 区仍有未勾项"

    _, todo = check_stage_compliance(_TASK, stage, STAGES.index(stage))
    assert todo == [], f"{stage}: 签署后仍被拦住: {todo}"

    # 签署过就不该再重复提示
    assert MonitorTUI._is_gate_signoff_needed(tui) is False


@pytest.mark.parametrize("stage", STAGES)
def test_signoff_only_touches_gate_region(task_dir, stage):
    """签署只代表「我批准推进」，不得替 agent 勾掉过程记录/下游待办。"""
    _write_agent_output(task_dir, stage)
    before = (task_dir / f"{stage}.md").read_text(encoding="utf-8")

    MonitorTUI._write_gate_signoff(_tui(stage))

    after = (task_dir / f"{stage}.md").read_text(encoding="utf-8")
    head_before = before[:before.rfind("## Gate")]
    head_after = after[:after.rfind("## Gate")]
    assert head_before == head_after, \
        f"{stage}: Gate 之外的内容被改动（过程记录被冒签）"


@pytest.mark.parametrize("stage", STAGES)
def test_no_signoff_prompt_while_agent_active(task_dir, stage):
    """agent 还在跑时不能弹签署 —— 那时工作尚未完成。"""
    _write_agent_output(task_dir, stage)
    assert MonitorTUI._is_gate_signoff_needed(_tui(stage, status="active")) is False


@pytest.mark.parametrize("stage", STAGES)
def test_no_signoff_prompt_before_agent_output(task_dir, stage):
    """agent 未产出（模板原样）时不提示，避免刚启动就要求签署。"""
    tpl = (TPLS / f"{stage}.md").read_text(encoding="utf-8")
    (task_dir / f"{stage}.md").write_text(tpl, encoding="utf-8")
    assert MonitorTUI._is_gate_signoff_needed(_tui(stage)) is False


def test_no_signoff_prompt_while_question_pending(task_dir):
    """agent 有待回答的提问时，签署入口不能抢占选项面板。"""
    _write_agent_output(task_dir, "03-coding")
    tui = _tui("03-coding")
    tui.state.pending_questions = [{"question": "选哪个?", "options": ["A. 甲"]}]
    assert MonitorTUI._is_gate_signoff_needed(tui) is False


def test_signoff_choice_a_ticks_gate_and_advances(task_dir):
    """用户按 [A] 批准 → 勾 Gate 并**直接推进**。

    签 Gate 表达的就是「可以走了」，批准之后再要求敲一次 /advance 是多余的
    一步。这里断言推进真的被触发，而不是断言那句提示文案。
    """
    _write_agent_output(task_dir, "03-coding")
    tui = _tui("03-coding")

    with patch.object(MonitorTUI, "_is_gate_signoff_needed", return_value=True), \
         patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor") as ex, \
         patch.object(MonitorTUI, "_run_advance") as adv:
        tui._dispatch("A")

    ex.return_value.answer.assert_not_called()
    content = (task_dir / "03-coding.md").read_text(encoding="utf-8")
    assert "[ ]" not in content[content.rfind("## Gate"):]
    assert adv.call_count == 1, \
        "签署后没有自动推进，用户还得再敲一次 /advance"


def test_signoff_does_not_advance_when_gate_write_fails(task_dir):
    """签署失败时不能推进 —— 那会绕过门禁。"""
    _write_agent_output(task_dir, "03-coding")
    tui = _tui("03-coding")

    with patch.object(MonitorTUI, "_is_gate_signoff_needed", return_value=True), \
         patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor"), \
         patch.object(MonitorTUI, "_write_gate_signoff", return_value=False), \
         patch.object(MonitorTUI, "_run_advance") as adv:
        tui._dispatch("A")

    adv.assert_not_called()
    assert any("失败" in msg for src, msg in tui.logs if src == "error"), tui.logs


def test_signoff_choice_b_returns_to_dialogue(task_dir):
    """按 [B] 要求修订 → 不勾 Gate，回到对话让 agent 继续改。"""
    _write_agent_output(task_dir, "03-coding")
    tui = _tui("03-coding")

    with patch.object(MonitorTUI, "_is_gate_signoff_needed", return_value=True), \
         patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor"):
        tui._dispatch("B")

    content = (task_dir / "03-coding.md").read_text(encoding="utf-8")
    assert "[ ]" in content[content.rfind("## Gate"):], "按 B 不应勾 Gate"
    assert tui.state.input_mode == "none"


def test_invalid_signoff_choice_does_not_fall_through(task_dir):
    """agent 已 idle，穿透去发回复没人接收；必须提示重选。"""
    _write_agent_output(task_dir, "03-coding")
    tui = _tui("03-coding")

    with patch.object(MonitorTUI, "_is_gate_signoff_needed", return_value=True), \
         patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor") as ex:
        tui._dispatch("Z")

    ex.return_value.answer.assert_not_called()
    assert any(src == "error" for src, _ in tui.logs), tui.logs


def test_review_routing_takes_priority_over_signoff(task_dir):
    """04 的 Route 未填时应先走路由选择，不能被签署入口顶掉。"""
    tpl = (TPLS / "04-review.md").read_text(encoding="utf-8")
    (task_dir / "04-review.md").write_text(
        tpl.replace("___", "已填写内容"), encoding="utf-8")  # Route 仍是占位
    tui = _tui("04-review")

    assert MonitorTUI._is_review_routing_needed(tui) is True
    assert MonitorTUI._is_gate_signoff_needed(tui) is False, \
        "Route 未定时不该先要求签 Gate"


# ── 多轮对话模式：产出还没落盘时也必须能签署 ──

@pytest.mark.parametrize("stage", STAGES)
def test_signoff_offered_when_output_only_in_log(task_dir, stage):
    """agent 已答完但产出尚未落盘时，签署入口仍要出现。

    e2e 复现：多轮模式下 ``_save_stage_output`` 只在 ``/advance`` 触发
    ``_stage_done`` 之后才执行，所以对话期间 {stage}.md 还是原始模板。
    若签署入口靠「文件里有产出」判断，它永远不会出现 —— 用户按 A 被
    输入校验拒绝，字符还留在缓冲区，把下一条命令污染成 ``A/advance``。
    真正的完成信号是 agent 已 idle 且本阶段有过对话产出。
    """
    tpl = (TPLS / f"{stage}.md").read_text(encoding="utf-8")
    (task_dir / f"{stage}.md").write_text(tpl, encoding="utf-8")  # 未回填
    if stage == "04-review":
        # Route 未决时路由选择优先，签署入口本就该让路；这里只验签署逻辑
        ss.write_route(_TASK, "05-Archive")
    tui = _tui(stage)
    tui.state.log_lines = [("agent", "## 🤖 AI Output\n### 结论\n- 采用方案 B")]

    assert MonitorTUI._is_gate_signoff_needed(tui) is True, \
        f"{stage}: agent 已产出（仅在对话里）却没有签署入口"


@pytest.mark.parametrize("stage", STAGES)
def test_no_signoff_when_agent_never_spoke(task_dir, stage):
    """agent 一句话都没说过 → 不提示签署（刚启动的空白阶段）。"""
    tpl = (TPLS / f"{stage}.md").read_text(encoding="utf-8")
    (task_dir / f"{stage}.md").write_text(tpl, encoding="utf-8")
    tui = _tui(stage)
    tui.state.log_lines = [("sw", "启动阶段"), ("user", "/status")]

    assert MonitorTUI._is_gate_signoff_needed(tui) is False, \
        f"{stage}: agent 未产出就要求签署"
