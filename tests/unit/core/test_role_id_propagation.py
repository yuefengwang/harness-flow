"""A4 的验收 6 / 13：role_id 从 factory 一路传到真正生效的权限规则表。

验收 13 是防「白做」的判据（A4 的 2.2 / 9.2）：
`Toolbox` 的白名单只被 `agents/gemini.py` 引用，对当前实际运行的 opencode
**一概不生效**（A0 的 2.5）。所以只断言 `get_tools_for_role()` 的返回值
等于没测 —— 必须断言 `OpenCodeAgent` 生成的 permission 规则表本身。
"""

import copy

import pytest

from sw_lib.core import config as C
from sw_lib.agents.opencode import OpenCodeAgent


@pytest.fixture(autouse=True)
def _restore_global_config():
    snapshot = copy.deepcopy(C._manager.config)
    yield
    C._manager._config = snapshot


def _role(agent="opencode", model="opencode/mimo-v2.5-free", tools=None):
    return C.RoleConfig(
        agent=agent, model=model, description="",
        tools=list(tools or ["list_files", "read_file", "ask_user"]),
    )


def _configure_two_reviewers():
    cfg = C._manager.config
    cfg.roles = {
        "developer": _role(tools=["list_files", "read_file", "write_file",
                                  "run_command", "ask_user"]),
        "reviewer": _role(tools=["list_files", "read_file", "run_command",
                                 "ask_user"]),
        "adversary": _role(model="opencode/mimo-v2.5-free",
                           tools=["list_files", "read_file", "ask_user"]),
        "design_critic": _role(model="opencode/critic-model",
                               tools=["list_files", "read_file"]),
    }
    cfg.stage_roles = {"03-coding": "developer", "04-review": "reviewer"}
    cfg.review = C.ReviewConfig(
        require_heterogeneous=False,
        subjective=[
            C.SubjectiveReviewer(role="adversary", model="opencode/mimo-v2.5-free",
                                 kind="counterexample"),
            C.SubjectiveReviewer(role="design_critic", model="opencode/critic-model",
                                 kind="design_review"),
        ],
    )
    return cfg


def _rules(stage, role_id=None):
    agent = object.__new__(OpenCodeAgent)
    agent.stage = stage
    agent.role_id = role_id
    agent.name = "pytest-a4-role-probe"
    agent._witness_phase = None
    return agent._permission_rules()


def _action_for(rules, permission):
    """按 findLast 语义取最终判定 —— 后者优先。"""
    hits = [r for r in rules if r["permission"] == permission]
    return hits[-1]["action"] if hits else None


# ── 验收 13：权限到达 opencode 侧规则表 ──

def test_design_critic_bash_is_denied_in_permission_rules():
    """design_critic 无 run_command，其 opencode 对应工具 bash 必须 deny。

    ⚠️ 强度声明（A4 的 1.3 / 3.8）：deny 掉 bash 只是规则层面的声明。
    A0 的 2.9.5 实测 assert 端点一律返回 allow，真实判定在工具执行路径。
    本条测的是「规则形状正确」，不是「真的拦住了」。
    """
    _configure_two_reviewers()
    rules = _rules("04-review", role_id="design_critic")

    assert _action_for(rules, "bash") == "deny"
    assert _action_for(rules, "write") == "deny"


def test_default_reviewer_still_allows_bash():
    """对照组：不传 role_id 时按 stage 解析，reviewer 有 run_command。

    没有这条对照，上一条可能因为「所有工具都被 deny」而假绿。
    """
    _configure_two_reviewers()
    rules = _rules("04-review")

    assert _action_for(rules, "bash") == "allow"


def test_adversary_keeps_question_tool():
    _configure_two_reviewers()
    rules = _rules("04-review", role_id="adversary")

    assert _action_for(rules, "question") == "allow"
    assert _action_for(rules, "bash") == "deny"


def test_unknown_role_id_falls_back_to_stage_and_warns(caplog):
    """R5：某条创建路径没传对 role_id 时回落，但**必须记录警告**。

    静默回落会让「纯只读的 design_critic」悄悄拿到 reviewer 的 run_command，
    与 2.3 的静默兜底同类。
    """
    _configure_two_reviewers()
    with caplog.at_level("WARNING"):
        rules = _rules("04-review", role_id="ghost_role")

    assert _action_for(rules, "bash") == "allow"  # 回落到 stage
    assert any("ghost_role" in r.getMessage() for r in caplog.records), \
        "role_id 未知时静默回落 —— 降级必须可见"


# ── 验收 6：factory 传 role_id ──

def test_factory_accepts_role_id_and_uses_role_model(dummy_task):
    from sw_lib.core.bootstrap import _make_agent_factory

    _configure_two_reviewers()
    factory = _make_agent_factory("04-review")

    # 用 dummy_task 夹具的任务名：OpenCodeAgent 构造会在 repo/<name> 建目录，
    # 自造名字（如 a4-factory-probe）不在 residue 的回收前缀里，会留孤儿。
    agent = factory("04-review", dummy_task, role_id="design_critic")
    try:
        assert agent.model_name == "opencode/critic-model"
        assert getattr(agent, "role_id", None) == "design_critic"
    finally:
        shutdown = getattr(agent, "shutdown", None)
        if callable(shutdown):
            shutdown()


def test_factory_without_role_id_keeps_stage_model(dummy_task):
    from sw_lib.core.bootstrap import _make_agent_factory

    _configure_two_reviewers()
    factory = _make_agent_factory("04-review")

    agent = factory("04-review", dummy_task)
    try:
        assert agent.model_name == "opencode/mimo-v2.5-free"
        assert getattr(agent, "role_id", None) is None
    finally:
        shutdown = getattr(agent, "shutdown", None)
        if callable(shutdown):
            shutdown()
