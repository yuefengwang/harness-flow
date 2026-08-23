"""A4 的验收 1-6 / 14：一阶段多角色的解析能力。

当前体系是一阶段一角色的单射（A4 的 1.1），于是「配两个 reviewer、各用
不同模型」做不到 —— 而那正是 C2「同权重模型盲区重合」的解法。

配置形态以 A5 的 `harness.review.subjective` 为准（A4 的 3.1），
`stage_roles` 保持不变，继续服务单角色回落与其余四个阶段。
"""

import copy

import pytest
import yaml

from sw_lib.core import config as C


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


def _two_reviewers():
    cfg = C._manager.config
    cfg.roles = {
        "reviewer": _role(model="opencode/mimo-v2.5-free"),
        "adversary": _role(model="opencode/mimo-v2.5-free",
                           tools=["list_files", "read_file"]),
        "design_critic": _role(agent="gemini", model="gemini/gemini-2.0-flash",
                               tools=["list_files", "read_file"]),
    }
    cfg.stage_roles = {"03-coding": "developer", "04-review": "reviewer"}
    cfg.review = C.ReviewConfig(
        objective_enabled=True,
        require_heterogeneous=False,
        subjective=[
            C.SubjectiveReviewer(role="adversary", model="opencode/mimo-v2.5-free",
                                 kind="counterexample"),
            C.SubjectiveReviewer(role="design_critic", model="gemini/gemini-2.0-flash",
                                 kind="design_review"),
        ],
    )
    return cfg


# ── 验收 1：多角色解析 ──

def test_stage_roles_returns_all_subjective_reviewers():
    _two_reviewers()
    assert C.resolve_stage_roles("04-review") == ["adversary", "design_critic"]


def test_other_stages_still_single_role():
    _two_reviewers()
    cfg = C._manager.config
    cfg.roles["developer"] = _role()
    assert C.resolve_stage_roles("03-coding") == ["developer"]


# ── 验收 2：空 subjective 回落（不得返回空列表）──

def test_empty_subjective_falls_back_to_stage_roles():
    cfg = C._manager.config
    cfg.roles = {"reviewer": _role()}
    cfg.stage_roles = {"04-review": "reviewer"}
    cfg.review = C.ReviewConfig(subjective=[])

    # 断言等于 ["reviewer"]，而非「长度 <= 1」—— 空列表意味着 04 阶段
    # 一个审查者都没有（A4 的 9.2）。
    assert C.resolve_stage_roles("04-review") == ["reviewer"]


def test_unknown_stage_returns_empty_list():
    assert C.resolve_stage_roles("99-nope") == []


# ── 验收 3：review 配置解析 ──

def test_resolve_review_config_parses_objective_and_kind():
    _two_reviewers()
    rc = C.resolve_review_config()

    assert rc.objective_enabled is True
    assert [r.kind for r in rc.subjective] == ["counterexample", "design_review"]


# ── 验收 4：按角色解析模型 ──

def test_model_resolves_per_role_and_differs_from_default_reviewer():
    _two_reviewers()

    critic = C.resolve_agent_model("04-review", "", role_id="design_critic")
    default = C.resolve_agent_model("04-review", "")

    assert critic == "gemini/gemini-2.0-flash"
    assert critic != default


def test_agent_type_resolves_per_role():
    _two_reviewers()
    assert C.resolve_agent_type("04-review", "", role_id="design_critic") == "gemini"
    assert C.resolve_agent_type("04-review", "", role_id="adversary") == "opencode"


def test_unknown_role_id_raises_rather_than_silently_falling_back():
    """R5 的反面：role_id 写错时不得静默退化成 stage 默认角色。"""
    _two_reviewers()
    with pytest.raises(C.ConfigError):
        C.resolve_agent_model("04-review", "", role_id="ghost")


# ── 验收 5：design_critic 无 run_command ──

def test_design_critic_tools_exclude_run_command():
    _two_reviewers()
    tools = C.get_tools_for_role("design_critic", "04-review")

    assert "run_command" not in tools
    assert "read_file" in tools


def test_get_tools_for_role_none_falls_back_to_stage():
    _two_reviewers()
    assert C.get_tools_for_role(None, "04-review") == C.get_tools_for_stage("04-review")


def test_subjective_tools_override_role_tools():
    """subjective[].tools 非空时覆盖 roles[role].tools（A4 的 3.2）。"""
    cfg = _two_reviewers()
    cfg.review.subjective[0].tools = ["read_file"]

    assert C.get_tools_for_role("adversary", "04-review") == ["read_file"]


# ── 验收 14：增删审查者不改 Python（必须走真实 YAML 加载）──

def test_adding_third_reviewer_needs_no_python_change(tmp_path, monkeypatch):
    """A4 的 9.2：不得在测试里用 Python 构造 ReviewConfig —— 那会绕过
    「只改 YAML」这个核心判据。必须写临时 YAML 并真实加载。
    """
    doc = {
        "harness": {
            "roles": {
                "developer": {"agent": "opencode", "model": "opencode/dev"},
                "adversary": {"agent": "opencode", "model": "opencode/a",
                              "tools": ["list_files", "read_file"]},
                "design_critic": {"agent": "gemini", "model": "gemini/g",
                                  "tools": ["list_files", "read_file"]},
                "third_eye": {"agent": "claudecode", "model": "anthropic/c",
                              "tools": ["list_files", "read_file"]},
                "reviewer": {"agent": "opencode", "model": "opencode/r"},
            },
            "stage_roles": {"03-coding": "developer", "04-review": "reviewer"},
            "review": {
                "objective": {"enabled": True},
                "require_heterogeneous": False,
                "subjective": [
                    {"role": "adversary", "model": "opencode/a",
                     "kind": "counterexample"},
                    {"role": "design_critic", "model": "gemini/g",
                     "kind": "design_review"},
                    {"role": "third_eye", "model": "anthropic/c",
                     "kind": "design_review"},
                ],
            },
        }
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr(C, "CONFIG_DIR", tmp_path)
    C._manager.reload()

    assert len(C.resolve_stage_roles("04-review")) == 3
    assert len(C.resolve_review_config().subjective) == 3
    assert C.resolve_agent_type("04-review", "", role_id="third_eye") == "claudecode"


def test_yaml_without_review_section_keeps_single_role(tmp_path, monkeypatch):
    """回滚路径（A4 的第 10 节）：删掉 review 段即回落单角色。"""
    doc = {
        "harness": {
            "roles": {"reviewer": {"agent": "opencode", "model": "opencode/r"}},
            "stage_roles": {"04-review": "reviewer"},
        }
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr(C, "CONFIG_DIR", tmp_path)
    C._manager.reload()

    assert C.resolve_stage_roles("04-review") == ["reviewer"]
    assert C.resolve_review_config().subjective == []
