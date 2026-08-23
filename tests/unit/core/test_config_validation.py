"""A4 的验收 10 / 11 / 12：配置写错不再静默兜底，同构配置被拒绝。

改造前实测（A4 的 2.3）：`stage_roles` 指向不存在的角色时，三个解析函数
分别静默返回 `"gemini"` / `"gemini-2.0-flash"` / 三件套只读工具，**无任何
警告**。A4 的全部目的是「用不同 provider 消除先验盲区」，而一个拼写错误
就让机制静默失效。

断言纪律（A4 的 9.2）：必须断言**抛 ConfigError**，不能只断言「返回值不是
gemini」—— 若实现改成返回 None，那条宽松断言也能过，而 None 会在下游炸得
更难查。
"""

import copy

import pytest

from sw_lib.core import config as C


@pytest.fixture(autouse=True)
def _restore_global_config():
    """deepcopy 还原全局单例（A4 的 9.1：嵌套 RoleConfig 浅拷贝还原不了）。"""
    snapshot = copy.deepcopy(C._manager.config)
    yield
    C._manager._config = snapshot


def _role(agent="opencode", model="opencode/mimo-v2.5-free", tools=None):
    return C.RoleConfig(
        agent=agent, model=model, description="",
        tools=list(tools or ["list_files", "read_file", "ask_user"]),
    )


def _errors(issues):
    return [i for i in issues if i.severity == "error"]


def _codes(issues):
    return {i.code for i in issues}


# ── 验收 10：stage_roles 指向不存在的角色 ──

def test_missing_role_is_reported_as_error():
    cfg = C._manager.config
    cfg.roles = {"reviewer": _role()}
    cfg.stage_roles = {"04-review": "nonexistent_role"}

    issues = C.validate_config()

    assert "stage_role_unknown" in _codes(_errors(issues))


def test_missing_role_raises_instead_of_silently_returning_gemini():
    """A4 的 2.3 回归锚点：改造前返回 "gemini"，改造后必须响亮失败。"""
    cfg = C._manager.config
    cfg.roles = {"reviewer": _role()}
    cfg.stage_roles = {"04-review": "nonexistent_role"}

    with pytest.raises(C.ConfigError):
        C.assert_config_valid()


def test_valid_config_reports_no_error():
    cfg = C._manager.config
    cfg.roles = {"reviewer": _role()}
    cfg.stage_roles = {"04-review": "reviewer"}
    cfg.review = C.ReviewConfig(objective_enabled=True, subjective=[])

    assert _errors(C.validate_config()) == []
    C.assert_config_valid()  # 不得抛


def test_suspicious_model_name_is_warn_not_error():
    """模型名格式可疑只记 warn（A4 的 3.4）—— 真实性到会话才知道（U4-2）。"""
    cfg = C._manager.config
    cfg.roles = {"reviewer": _role(model="typomodelxxx")}
    cfg.stage_roles = {"04-review": "reviewer"}

    issues = C.validate_config()

    assert "model_format_suspicious" in _codes(issues)
    assert _errors(issues) == []


# ── 验收 3 / 10：review.subjective 的校验 ──

def test_subjective_role_not_in_roles_is_error():
    cfg = C._manager.config
    cfg.roles = {"reviewer": _role()}
    cfg.stage_roles = {"04-review": "reviewer"}
    cfg.review = C.ReviewConfig(
        subjective=[C.SubjectiveReviewer(role="ghost", model="x/y",
                                         kind="design_review")],
    )

    assert "subjective_role_unknown" in _codes(_errors(C.validate_config()))


def test_unknown_kind_is_error():
    cfg = C._manager.config
    cfg.roles = {"reviewer": _role()}
    cfg.stage_roles = {"04-review": "reviewer"}
    cfg.review = C.ReviewConfig(
        subjective=[C.SubjectiveReviewer(role="reviewer", model="x/y",
                                         kind="vibes")],
    )

    assert "subjective_kind_unknown" in _codes(_errors(C.validate_config()))


def test_no_reviewer_at_all_is_error():
    """subjective 为空且 objective 关闭 —— 04 阶段将无任何审查者。"""
    cfg = C._manager.config
    cfg.roles = {"reviewer": _role()}
    cfg.stage_roles = {"04-review": "reviewer"}
    cfg.review = C.ReviewConfig(objective_enabled=False, subjective=[])

    assert "review_has_no_reviewer" in _codes(_errors(C.validate_config()))


# ── 验收 7：provider 派生 ──

def test_provider_from_model_prefix():
    assert _role(model="opencode/mimo-v2.5-free").provider == "opencode"


def test_provider_falls_back_to_agent_when_model_has_no_slash():
    assert _role(agent="gemini", model="gemini-2.0-flash").provider == "gemini"


def test_explicit_provider_overrides_inference():
    """R3：自建网关代理多家模型时，允许显式声明覆盖推断值。"""
    role = C.RoleConfig(agent="opencode", model="gateway/anything",
                        provider="anthropic")
    assert role.provider == "anthropic"


# ── 验收 11：审查者之间同 provider 被拒 ──

def test_same_provider_reviewers_rejected_when_heterogeneous_required():
    cfg = C._manager.config
    cfg.roles = {
        "developer": _role(model="gemini/gemini-2.0-flash"),
        "adversary": _role(model="opencode/mimo-v2.5-free"),
        "design_critic": _role(model="opencode/other-model"),
    }
    cfg.stage_roles = {"03-coding": "developer", "04-review": "adversary"}
    cfg.review = C.ReviewConfig(
        require_heterogeneous=True,
        subjective=[
            C.SubjectiveReviewer(role="adversary", model="opencode/mimo-v2.5-free",
                                 kind="counterexample"),
            C.SubjectiveReviewer(role="design_critic", model="opencode/other-model",
                                 kind="design_review"),
        ],
    )

    assert "reviewers_share_provider" in _codes(_errors(C.validate_config()))
    with pytest.raises(C.ConfigError):
        C.assert_config_valid()


# ── 验收 12：审查者与 developer 同 provider 也被拒（最容易漏的一条）──

def test_reviewer_sharing_provider_with_developer_rejected():
    """A4 的 9.2：只校验审查者互异是不够的 —— 与**作者**盲区重合同样致命。

    本用例的两个审查者 provider 互不相同（gemini / opencode），所以
    「审查者互异」这条校验不会触发；触发的必须是「与 developer 同源」。
    """
    cfg = C._manager.config
    cfg.roles = {
        "developer": _role(model="opencode/mimo-v2.5-free"),
        "adversary": _role(model="opencode/mimo-v2.5-free"),
        "design_critic": _role(agent="gemini", model="gemini/gemini-2.0-flash"),
    }
    cfg.stage_roles = {"03-coding": "developer", "04-review": "adversary"}
    cfg.review = C.ReviewConfig(
        require_heterogeneous=True,
        subjective=[
            C.SubjectiveReviewer(role="adversary", model="opencode/mimo-v2.5-free",
                                 kind="counterexample"),
            C.SubjectiveReviewer(role="design_critic", model="gemini/gemini-2.0-flash",
                                 kind="design_review"),
        ],
    )

    issues = _errors(C.validate_config())
    codes = _codes(issues)
    assert "reviewer_shares_provider_with_developer" in codes
    assert "reviewers_share_provider" not in codes  # 证明触发的是与作者同源那条


def test_heterogeneous_violation_tolerated_when_switch_off():
    """require_heterogeneous: false 时不阻塞，但降级必须可见（A4 的 3.5 / R2）。"""
    cfg = C._manager.config
    cfg.roles = {
        "developer": _role(model="opencode/mimo-v2.5-free"),
        "adversary": _role(model="opencode/mimo-v2.5-free"),
        "design_critic": _role(model="opencode/mimo-v2.5-free"),
    }
    cfg.stage_roles = {"03-coding": "developer", "04-review": "adversary"}
    cfg.review = C.ReviewConfig(
        require_heterogeneous=False,
        subjective=[
            C.SubjectiveReviewer(role="adversary", model="opencode/mimo-v2.5-free",
                                 kind="counterexample"),
            C.SubjectiveReviewer(role="design_critic", model="opencode/mimo-v2.5-free",
                                 kind="design_review"),
        ],
    )

    issues = C.validate_config()
    assert _errors(issues) == []
    # 降级不得静默：仍须留下 warn 供 A10 报告标注「审查者同构」。
    assert "reviewers_homogeneous_degraded" in _codes(issues)
    assert C.resolve_review_config().heterogeneous_status == "degraded"


def test_mock_mode_marks_heterogeneity_skipped_not_passed():
    """A4 的第 4 节：mock 只有一个 provider，跳过校验但不记为通过。"""
    cfg = C._manager.config
    cfg.roles = {
        "developer": _role(model="opencode/mimo-v2.5-free"),
        "adversary": _role(model="opencode/mimo-v2.5-free"),
        "design_critic": _role(model="opencode/mimo-v2.5-free"),
    }
    cfg.stage_roles = {"03-coding": "developer", "04-review": "adversary"}
    cfg.mock_agent.enabled = True
    cfg.review = C.ReviewConfig(
        require_heterogeneous=True,
        subjective=[
            C.SubjectiveReviewer(role="adversary", model="opencode/mimo-v2.5-free",
                                 kind="counterexample"),
            C.SubjectiveReviewer(role="design_critic", model="opencode/mimo-v2.5-free",
                                 kind="design_review"),
        ],
    )

    assert _errors(C.validate_config()) == []
    assert C.resolve_review_config().heterogeneous_status == "mock_skipped"


# ── 9.1 第 3 条：测试不得改动磁盘配置 ──

def test_validation_does_not_touch_config_file_on_disk():
    path = C.CONFIG_DIR / "config.yaml"
    before = path.read_bytes()

    cfg = C._manager.config
    cfg.stage_roles = {"04-review": "nonexistent_role"}
    C.validate_config()

    assert path.read_bytes() == before
