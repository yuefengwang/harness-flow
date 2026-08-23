"""A5 与 A9 的边界契约：多审查者模式下谁负责回填 route / gate_passed。

探针实测（A5 的 11.1）：配两个主观审查者后 `StageOutput.route` 为 `None`、
`gate_passed` 为 `False` —— 因为仲裁器（A9）还没实现，fan-in 之后没人把
多份 findings 归约成一个阶段结论。TUI 靠 route 推进，所以此时 04 阶段
**推不动**。

这类缺口最危险的形态是「配置填上去、流程静默卡住」。所以这里用测试把
当前边界钉住：
  1. 单审查者路径必须仍能推进（既有行为不许坏）。
  2. 多审查者路径必须**显式可识别**为「等待仲裁」，而不是伪装成一个
     gate_passed=False 的普通失败。
"""

import copy

import pytest

from sw_lib.core import config as C
from sw_lib.workflow import review_graph as RG


@pytest.fixture(autouse=True)
def _restore_global_config():
    snapshot = copy.deepcopy(C._manager.config)
    yield
    C._manager._config = snapshot


def _set_reviewers(n):
    names = ["adversary", "design_critic", "third_eye"][:n]
    cfg = C._manager.config
    cfg.roles = {x: C.RoleConfig(agent="opencode", model="m", description="", tools=[])
                 for x in names}
    cfg.roles.setdefault("reviewer", C.RoleConfig(agent="opencode", model="m",
                                                 description="", tools=[]))
    cfg.stage_roles = {"04-review": "reviewer"}
    cfg.review = C.ReviewConfig(
        objective_enabled=True,
        subjective=[C.SubjectiveReviewer(role=x, model=f"m{i}", kind="k")
                    for i, x in enumerate(names)])


def test_multi_reviewer_needs_arbiter_is_explicit():
    """多审查者时必须显式报告「待仲裁」，不得静默返回无结论。"""
    _set_reviewers(2)
    assert RG.needs_arbiter() is True


def test_single_reviewer_does_not_need_arbiter():
    """单审查者回落路径自己就有结论，不需要仲裁 —— 既有行为不变。"""
    _set_reviewers(0)
    assert RG.needs_arbiter() is False


def test_arbiter_unimplemented_is_reported_not_silent():
    """A9 未落地这件事必须能被程序查出来。

    否则「填了配置就卡住」只能靠人肉发现 —— 那正是本项目要消除的失效模式。
    """
    _set_reviewers(2)
    status = RG.arbiter_status()
    assert status["implemented"] is False
    assert status["owner"] == "A9"
    # 不得把「未实现」表述成通过或无发现。
    assert status["verdict"] not in ("ok", "no_finding", "passed")


def test_config_does_not_enable_multi_reviewers_before_arbiter():
    """守护栏：仓库配置在 A9 落地前不得启用多审查者。

    实测填上两个审查者会让 route 为空、TUI 推不动 04 阶段。
    A9 实现后删掉这条测试并同时填配置。
    """
    C._manager.reload()
    live = C._manager.config.review
    if len(live.subjective) >= 2:
        assert RG.arbiter_status()["implemented"] is True, (
            "config.yaml 启用了多审查者，但仲裁器（A9）尚未实现 —— "
            "04 阶段会因 route 为空而卡住")
