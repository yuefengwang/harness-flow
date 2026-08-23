"""provider 必须识别**模型族**，而不是网关名。

`opencode` 是一个网关，背后代理了 29 个不同厂商的免费模型
（glm / kimi / minimax / mimo / nemotron / ling …）。

原实现取 `model.split("/")[0]`，于是 `opencode/glm-5-free` 与
`opencode/kimi-k2.5-free` 都返回 `opencode` —— 两个明明不同厂商的模型被判为
同构，`require_heterogeneous: true` 直接报错。实测确认过这个行为。

后果不是「多报一个错」，而是**异构性根本无法开启**：C2 要的
「审查者与作者盲区不重合」永远停在 degraded，而报告会显示已配置异构。

⚠️ 强度声明：模型族仍是廉价代理指标，不是先验独立性的证明（A4 的 U4-3）。
不同厂商若共享训练数据，盲区仍可能重合。真正的证明只能来自 A11 的变异探针。
"""

import copy

import pytest

from sw_lib.core import config as C


@pytest.fixture(autouse=True)
def _restore_global_config():
    snapshot = copy.deepcopy(C._manager.config)
    yield
    C._manager._config = snapshot


# ── provider 解析本身 ──

@pytest.mark.parametrize("model,expected", [
    ("opencode/glm-5-free", "glm"),
    ("opencode/kimi-k2.5-free", "kimi"),
    ("opencode/minimax-m3-free", "minimax"),
    ("opencode/mimo-v2.5-free", "mimo"),
    ("opencode/nemotron-3-super-free", "nemotron"),
    ("opencode/ling-3.0-flash-free", "ling"),
    ("opencode/deepseek-v4-flash-free", "deepseek"),
    ("opencode/qwen3.6-plus-free", "qwen"),
    ("opencode/grok-code", "grok"),
    ("opencode/gpt-5.4-mini", "gpt"),
    ("opencode/claude-sonnet-4-5", "claude"),
    ("opencode/gemini-3-pro", "gemini"),
])
def test_provider_identifies_model_family_not_gateway(model, expected):
    role = C.RoleConfig(agent="opencode", model=model, description="", tools=[])
    assert role.provider == expected, (
        f"{model} 的 provider 应是模型族 {expected!r}，"
        f"实际 {role.provider!r} —— 网关名无法区分厂商")


def test_explicit_override_still_wins():
    """R3：自建网关可能用一个前缀代理多家模型，显式声明必须优先。"""
    role = C.RoleConfig(agent="opencode", model="opencode/glm-5-free",
                        description="", tools=[], provider="my-gateway")
    assert role.provider == "my-gateway"


def test_model_without_slash_falls_back_to_family_then_agent():
    """无 `/` 时仍应尽量识别模型族，识别不出才回落 agent。"""
    assert C.RoleConfig(agent="gemini", model="gemini-2.0-flash",
                        description="", tools=[]).provider == "gemini"
    assert C.RoleConfig(agent="someagent", model="",
                        description="", tools=[]).provider == "someagent"


# ── 异构校验的实际效果 ──

def _setup(dev_model, adv_model, critic_model, require=True):
    cfg = C._manager.config
    cfg.roles = {
        "developer": C.RoleConfig(agent="opencode", model=dev_model, description="", tools=[]),
        "adversary": C.RoleConfig(agent="opencode", model=adv_model, description="", tools=[]),
        "design_critic": C.RoleConfig(agent="opencode", model=critic_model, description="", tools=[]),
    }
    cfg.stage_roles = {"03-coding": "developer", "04-review": "reviewer"}
    cfg.review = C.ReviewConfig(
        objective_enabled=True, require_heterogeneous=require,
        subjective=[
            C.SubjectiveReviewer(role="adversary", model=adv_model, kind="counterexample"),
            C.SubjectiveReviewer(role="design_critic", model=critic_model, kind="design_review"),
        ])
    return cfg


def test_three_distinct_families_pass_heterogeneity():
    """作者与两个审查者各用不同厂商 —— 必须无违规，否则异构无法开启。"""
    _setup("opencode/mimo-v2.5-free", "opencode/glm-5-free", "opencode/kimi-k2.5-free")
    codes = {i.code for i in C._heterogeneity_issues(C._manager.config)}
    assert codes == set(), f"三个不同厂商仍被判违规：{codes}"


def test_same_family_different_versions_still_violates():
    """同厂商不同版本仍算同构 —— 盲区重合看的是厂商，不是版本号。"""
    _setup("opencode/mimo-v2.5-free", "opencode/glm-5-free", "opencode/glm-4.7-free")
    codes = {i.code for i in C._heterogeneity_issues(C._manager.config)}
    assert "reviewers_share_provider" in codes


def test_reviewer_sharing_family_with_author_violates():
    """审查者与作者同厂商必须被挡住 —— 这是 C2 的原始诉求。"""
    _setup("opencode/glm-5-free", "opencode/glm-4.7-free", "opencode/kimi-k2.5-free")
    codes = {i.code for i in C._heterogeneity_issues(C._manager.config)}
    assert "reviewer_shares_provider_with_developer" in codes


def test_heterogeneous_config_reports_ok_not_degraded():
    """异构配置必须让状态变成 ok。

    停在 degraded 意味着 A10 的报告会一直标注「审查者同构」，
    而实际上已经配了不同厂商 —— 那是把已兑现的能力报成没兑现。
    """
    _setup("opencode/mimo-v2.5-free", "opencode/glm-5-free", "opencode/kimi-k2.5-free",
           require=False)
    assert C.resolve_review_config().heterogeneous_status == "ok"
