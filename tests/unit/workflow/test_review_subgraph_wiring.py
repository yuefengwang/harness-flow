"""A5 的 4.1：子图必须真的接在 build_harness_graph 上。

单测里 build_review_graph 全绿**不等于**机制接通 —— 如果 build_harness_graph
仍走 add_node(s.stage) 的老路，配置里写几个审查者都只会跑一个，
而所有 review_graph 的测试依然是绿的。这类假绿是 A5 第 8 节点名的风险。

所以这里断言运行时**真实启动的 agent 数量**，不断言图里有没有某个节点名。
"""

import copy

import pytest

from sw_lib.core import config as C
from sw_lib.workflow import review_graph as RG
from sw_lib.workflow.base import StageInput, StageOutput, StageRunnable
from sw_lib.workflow.graph import build_harness_graph, LangGraphAdapter


@pytest.fixture(autouse=True)
def _restore_global_config():
    snapshot = copy.deepcopy(C._manager.config)
    yield
    C._manager._config = snapshot


def _role():
    return C.RoleConfig(agent="opencode", model="opencode/m", description="",
                        tools=["list_files", "read_file"])


def _configure_reviewers(names):
    cfg = C._manager.config
    cfg.roles = dict(cfg.roles or {})
    for n in names:
        cfg.roles[n] = _role()
    cfg.roles.setdefault("reviewer", _role())
    cfg.stage_roles = dict(cfg.stage_roles or {})
    cfg.stage_roles["04-review"] = "reviewer"
    cfg.review = C.ReviewConfig(
        objective_enabled=False,
        subjective=[C.SubjectiveReviewer(role=n, model=f"opencode/m-{i}", kind="k")
                    for i, n in enumerate(names)],
        max_parallel=4,
    )


class _RecordingStage(StageRunnable):
    """记录每次 invoke 的 role_id，用来数「真的跑了几个审查者」。"""

    def __init__(self, stage, stage_idx, calls, role_id=None):
        from unittest.mock import MagicMock
        super().__init__(stage=stage, stage_idx=stage_idx,
                         context_builder=MagicMock(), output_parser=MagicMock(),
                         gate_validator=MagicMock(), agent_factory=MagicMock(),
                         role_id=role_id)
        self._calls = calls

    def invoke(self, input: StageInput) -> StageOutput:
        self._calls.append(getattr(self, "role_id", None))
        return StageOutput(task_name=input.task_name, stage=self.stage,
                           raw_agent_output="ok", parsed={}, gate_passed=True,
                           route=None)


def _stages(calls):
    names = ["01-brainstorming", "02-planning", "03-coding", "04-review", "05-archive"]
    return [_RecordingStage(n, i, calls) for i, n in enumerate(names)]


def test_two_reviewers_actually_invoke_two_agents(dummy_task):
    """验收 1 的真身：04 阶段实际启动 2 个审查者。"""
    _configure_reviewers(["adversary", "design_critic"])
    calls = []
    stages = _stages(calls)
    adapter = LangGraphAdapter(build_harness_graph(stages), stages)

    adapter.invoke(StageInput(task_name=dummy_task, stage="04-review", stage_idx=3))

    assert len(calls) == 2, f"配置了 2 个审查者，实际只跑了 {len(calls)} 个：{calls}"
    assert sorted(c for c in calls if c) == ["adversary", "design_critic"]


def test_three_reviewers_without_python_change(dummy_task):
    """验收 2：改成 3 个只动配置，不动 Python。"""
    _configure_reviewers(["adversary", "design_critic", "third_eye"])
    calls = []
    stages = _stages(calls)
    adapter = LangGraphAdapter(build_harness_graph(stages), stages)

    adapter.invoke(StageInput(task_name=dummy_task, stage="04-review", stage_idx=3))

    assert len(calls) == 3, f"配置了 3 个审查者，实际跑了 {len(calls)} 个：{calls}"


def test_single_role_config_runs_exactly_once(dummy_task):
    """验收 7 的向后兼容：subjective 为空时行为与改造前一致 —— 跑且只跑一次。"""
    cfg = C._manager.config
    cfg.roles = {"reviewer": _role()}
    cfg.stage_roles = {"04-review": "reviewer"}
    cfg.review = C.ReviewConfig(objective_enabled=False, subjective=[])
    calls = []
    stages = _stages(calls)
    adapter = LangGraphAdapter(build_harness_graph(stages), stages)

    out = adapter.invoke(StageInput(task_name=dummy_task, stage="04-review", stage_idx=3))

    assert len(calls) == 1, f"单角色配置应只跑 1 次，实际 {len(calls)}"
    assert out.stage == "04-review"


def test_other_stages_structure_unchanged(dummy_task):
    """验收 7：其余四阶段结构不变，仍是一阶段一 invoke。"""
    _configure_reviewers(["adversary", "design_critic"])
    calls = []
    stages = _stages(calls)
    adapter = LangGraphAdapter(build_harness_graph(stages), stages)

    adapter.invoke(StageInput(task_name=dummy_task, stage="03-coding", stage_idx=2))

    assert len(calls) == 1, f"03-coding 不该 fan-out，实际跑了 {len(calls)} 次"


def test_reviewer_failure_does_not_break_stage(dummy_task):
    """验收 5 在真实接线上的体现：一个审查者炸了，阶段不整体崩。"""
    _configure_reviewers(["adversary", "design_critic"])
    calls = []
    stages = _stages(calls)

    review = stages[3]
    original = review.invoke

    def flaky(input):
        role = getattr(review, "role_id", None)
        raise RuntimeError("审查者炸了")

    # 让 04 阶段的每次 invoke 都抛，阶段仍须返回而不是把异常抛给 TUI。
    review.invoke = flaky
    out = adapter_invoke_safely(stages, dummy_task)
    assert out is not None, "审查者异常把整个阶段带崩了"


def adapter_invoke_safely(stages, task):
    adapter = LangGraphAdapter(build_harness_graph(stages), stages)
    return adapter.invoke(StageInput(task_name=task, stage="04-review", stage_idx=3))
