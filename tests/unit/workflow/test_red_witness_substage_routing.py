"""A2 的 3.2：03a / 03b 是 03-coding 的**子状态**，不进 STAGES。

为什么这批用例必须存在：见证机制若只改钩子，03a 一准出就会沿 STAGES 链
推进到 04-review —— 实现阶段被整个跳过，机制沦为装饰。
子阶段的存在必须体现在**推进行为**上。

不改 `STAGES`（`core/config.py:28`）是刻意的：它被 TUI、Web、状态机多处
依赖，改它会波及 `stage_idx` 语义与 `entry_router`，风险远超收益。
"""

import pytest

from sw_lib.core import state as state_mod
from sw_lib.core.config import STAGES
from sw_lib.workflow import red_witness as rw
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.runtime import WorkflowRuntime


@pytest.fixture
def coding_task(tmp_path, monkeypatch):
    """一个停在 03-coding、Gate 已签署的任务。"""
    monkeypatch.setattr(state_mod, "TASKS", tmp_path)
    monkeypatch.setattr(ss, "TASKS", tmp_path)
    # STATUS 必须一起隔离：advance 内部会 upsert_task_summary，
    # 只改 TASKS 会把条目写进真实 workspace/STATUS.json，而 tmp_path
    # 一销毁就再也没人认识这个名字 —— 实测留下过 t-substage / t-other。
    monkeypatch.setattr(state_mod, "STATUS", tmp_path / "STATUS.json")
    monkeypatch.setattr(rw, "read_state", state_mod.read_state)
    name = "t-substage"
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    state_mod.write_state(name, {
        "id": name, "stage": "03-coding",
        "stage_idx": STAGES.index("03-coding"),
        "stage_status": "running",
    })
    return name


def test_phase_03a_advance_stays_in_coding(coding_task):
    """03a 见证完成后推进，必须留在 03-coding 做实现。"""
    rw.record_red(coding_task,
                  rw.WitnessVerdict("ok", "", 1, failed_nodes=["t.py::a"]),
                  {"t.py": "a" * 64})
    # record_red 已把 phase 推到 03b —— 手工退回 03a 模拟「刚写完测试」
    rw.request_rewitness(coding_task, reason="模拟停在 03a")
    assert rw.read_phase(coding_task) == "03a"

    st = WorkflowRuntime.advance(coding_task)

    assert st["stage"] == "03-coding", \
        f"03a 准出后直接跳到 {st['stage']} —— 实现阶段被跳过，机制沦为装饰"
    assert st["stage_idx"] == STAGES.index("03-coding")


def test_phase_03b_advance_moves_to_review(coding_task):
    """03b 转绿后才允许离开 03-coding。"""
    rw.record_red(coding_task,
                  rw.WitnessVerdict("ok", "", 1, failed_nodes=["t.py::a"]),
                  {"t.py": "a" * 64})
    rw.record_green(coding_task,
                    rw.WitnessVerdict("ok", "", 0, passed_nodes=["t.py::a"]))
    assert rw.read_phase(coding_task) == "03b"

    st = WorkflowRuntime.advance(coding_task)
    assert st["stage"] == "04-review", \
        f"03b 已转绿却没能推进，停在 {st['stage']}"


def test_stages_constant_is_untouched():
    """A2 的 3.2：不得往 STAGES 里加 03a / 03b 条目。"""
    assert STAGES == ["01-brainstorming", "02-planning", "03-coding",
                      "04-review", "05-archive"], \
        "STAGES 被改动 —— 会波及 stage_idx 语义与 entry_router"


def test_gate_is_reset_when_staying_for_impl(coding_task):
    """留在 03-coding 做实现时，Gate 必须回到未签署。

    否则 03a 的签名会让 03b 直接放行 —— 用户根本没为实现签过字。
    """
    rw.record_red(coding_task,
                  rw.WitnessVerdict("ok", "", 1, failed_nodes=["t.py::a"]),
                  {"t.py": "a" * 64})
    rw.request_rewitness(coding_task, reason="模拟停在 03a")
    ss.sign_gate(coding_task, "03-coding")
    assert ss.read_gate(coding_task, "03-coding").signed

    WorkflowRuntime.advance(coding_task)

    assert not ss.read_gate(coding_task, "03-coding").signed, \
        "留在 03-coding 时 Gate 仍是已签署 —— 03b 会被上一轮的签名直接放行"


def test_other_stages_are_unaffected(tmp_path, monkeypatch):
    """子阶段逻辑不得影响 03 之外的阶段（单调性）。"""
    monkeypatch.setattr(state_mod, "TASKS", tmp_path)
    monkeypatch.setattr(ss, "TASKS", tmp_path)
    monkeypatch.setattr(state_mod, "STATUS", tmp_path / "STATUS.json")
    name = "t-other"
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    state_mod.write_state(name, {
        "id": name, "stage": "02-planning",
        "stage_idx": STAGES.index("02-planning"), "stage_status": "running",
    })

    st = WorkflowRuntime.advance(name)
    assert st["stage"] == "03-coding", st["stage"]
