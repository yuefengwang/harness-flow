"""04-review 返工循环相关缺陷（任务 T3）。

现场表现：用户在 04-review 里连续四次选「05-Archive 归档」，流程却每次都推回
03-coding，在 03↔04 之间循环了六轮，而且返工回去的 agent 什么都没补。

三个独立缺陷叠在一起：

1. 推进离开 04-review 时不清 Route。第一轮选的 03-coding 一直留在 .state 里，
   回到 04 时 read_route 立刻返回旧值，用户的新选择完全无效。
2. TUI 的 _is_review_routing_needed 以「Route 为空」判断是否需要路由面板，
   于是第二轮起连 A/B/C 选项都不显示 —— 用户没有任何入口改这个决定。
3. 返工上下文里没有真实理由。Evidence 表被写死成「需返工修复的问题 /
   详见审查结论」，agent 拿到这种描述无从下手（用户要求补 README.md，
   agent 连续三轮只是把原代码重新确认一遍）。
"""
import shutil

import pytest

from sw_lib.core.config import TASKS, STAGES
from sw_lib.core.state import read_state, write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.runtime import WorkflowRuntime


@pytest.fixture
def review_task():
    """停在 04-review、Gate 已签、Route 已选 03-coding 的任务。"""
    name = "pytest-reroute-cycle"
    d = TASKS / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    write_state(name, {
        "id": name, "stage": "04-review",
        "stage_idx": STAGES.index("04-review"), "stage_status": "running",
    })
    for stage in ("03-coding", "04-review"):
        (d / f"{stage}.md").write_text(f"# {stage}\n", encoding="utf-8")
    yield name
    shutil.rmtree(d, ignore_errors=True)


def test_route_is_cleared_after_advancing(review_task):
    """推进离开 04-review 后，Route 不能留在 .state 里。

    留着就等于把第一次的选择变成永久决定：回到 04 时 read_route 直接返回旧值，
    用户之后选什么都不生效。
    """
    ss.sign_gate(review_task, "04-review")
    assert ss.write_route(review_task, "03-Coding")

    WorkflowRuntime.advance(review_task)

    assert read_state(review_task)["stage"] == "03-coding"
    assert ss.read_route(review_task) is None, \
        "旧 Route 未清除，回到 04-review 会无视用户的新选择"


def test_second_round_without_new_route_moves_forward(review_task):
    """完整复现死循环：04→03→04 后再推进，不能又被弹回 03-coding。

    这里故意不写第二轮的 Route —— 现场就是这样：面板不再显示，用户按 A 只是
    被当成普通消息，.state 里始终只有第一轮那个 03-coding。此时推进必须退回
    线性链路走向 05-archive，而不是照着过期的 target 再返工一次。
    """
    ss.sign_gate(review_task, "04-review")
    ss.write_route(review_task, "03-Coding")
    WorkflowRuntime.advance(review_task)              # 04 -> 03
    assert read_state(review_task)["stage"] == "03-coding"

    # 03 推进回 04
    ss.sign_gate(review_task, "03-coding")
    WorkflowRuntime.advance(review_task)
    assert read_state(review_task)["stage"] == "04-review"

    ss.sign_gate(review_task, "04-review")
    WorkflowRuntime.advance(review_task)

    assert read_state(review_task)["stage"] == "05-archive", \
        "过期 Route 仍在生效，04→03 死循环未修"


def test_second_round_route_choice_takes_effect(review_task):
    """第二轮显式改选归档时，新决定必须覆盖上一轮的 target。"""
    ss.sign_gate(review_task, "04-review")
    ss.write_route(review_task, "03-Coding")
    WorkflowRuntime.advance(review_task)              # 04 -> 03
    ss.sign_gate(review_task, "03-coding")
    WorkflowRuntime.advance(review_task)              # 03 -> 04

    ss.sign_gate(review_task, "04-review")
    assert ss.write_route(review_task, "05-Archive")
    WorkflowRuntime.advance(review_task)

    assert read_state(review_task)["stage"] == "05-archive", \
        "第二轮选择归档却没有生效"


def test_routing_panel_available_again_after_reroute(review_task):
    """回到 04-review 时必须重新出现路由面板（Route 为空）。"""
    from sw_lib.ui.tui import MonitorTUI

    ss.sign_gate(review_task, "04-review")
    ss.write_route(review_task, "03-Coding")
    WorkflowRuntime.advance(review_task)
    ss.sign_gate(review_task, "03-coding")
    WorkflowRuntime.advance(review_task)

    tui = type("T", (), {})()
    tui.state = type("S", (), {
        "stage": "04-review", "name": review_task,
        "agent_status": "idle", "pending_questions": [],
    })()
    assert MonitorTUI._is_review_routing_needed(tui) is True, \
        "Route 未清空导致路由面板不再显示，用户无从改选"


def test_linear_advance_does_not_touch_route(review_task):
    """非 04-review 阶段推进不该动 Route —— 免得把用户刚做的决定擦掉。"""
    st = read_state(review_task)
    st["stage"] = "03-coding"
    st["stage_idx"] = STAGES.index("03-coding")
    write_state(review_task, st)
    ss.write_route(review_task, "05-Archive")
    ss.sign_gate(review_task, "03-coding")

    WorkflowRuntime.advance(review_task)

    assert ss.read_route(review_task) == "05-archive", \
        "从 03 推进时不应清除 04-review 的 Route"


def test_forward_route_is_kept_for_audit(review_task):
    """前进到归档的决策要留着 —— 那不会再回到 04，清掉只会丢掉审计痕迹。

    04 的硬校验读 `sw state get <task> 04-review route`，没有值就报「缺少
    Route 决策」。归档后重跑钩子、事后复盘都依赖它。
    """
    ss.sign_gate(review_task, "04-review")
    ss.write_route(review_task, "05-Archive")

    WorkflowRuntime.advance(review_task)

    assert read_state(review_task)["stage"] == "05-archive"
    assert ss.read_route(review_task) == "05-archive", \
        "归档决策被清掉，04-review 的硬校验事后重跑会失败"


def test_voided_route_is_archived_to_history(review_task):
    """作废不是删除：返工的决策要能在 route_history 里查到。"""
    ss.sign_gate(review_task, "04-review")
    ss.write_route(review_task, "03-Coding")
    WorkflowRuntime.advance(review_task)

    assert ss.read_route(review_task) is None
    history = ss.read_route_history(review_task)
    assert [h.get("target") for h in history] == ["03-coding"], history


def test_history_accumulates_across_rounds(review_task):
    """多轮返工的决策按先后顺序全部留痕。"""
    for target in ("03-Coding", "02-Planning"):
        st = read_state(review_task)
        st["stage"] = "04-review"
        st["stage_idx"] = STAGES.index("04-review")
        write_state(review_task, st)
        ss.sign_gate(review_task, "04-review")
        ss.write_route(review_task, target)
        WorkflowRuntime.advance(review_task)

    history = [h.get("target") for h in ss.read_route_history(review_task)]
    assert history == ["03-coding", "02-planning"], history
