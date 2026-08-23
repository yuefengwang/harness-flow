"""B0 的验收 1、2：前置检查与环境指纹。

这是采集的第 1 步，也是**唯一事后无法补证**的一步（B0 第 6 节）：
即便后面的步骤来不及做，「采集时刻 A6-A9 尚未实施」这个事实
也必须先钉在时间线上。

判据是**文件是否存在**而不是文档里的标记 —— B0 的 2.1 明写
「实施前必须重跑这一检查」，而 PARALLEL.md 的教训是文档标记会过期。
"""

import json
import subprocess
from pathlib import Path

import pytest

from sw_lib.probe import baseline as bl

ROOT = Path(__file__).resolve().parents[3]


# ── 验收 1：识别 A6-A9 的实施状态 ──

def test_review_side_modules_are_the_four_plus_probe():
    """被检查的对象必须是审查侧四件套（A6-A9 的核心产出）。

    少一个就意味着那个维度的污染检不出来，而基线会自称干净。
    """
    names = {Path(p).name for p in bl.REVIEW_SIDE_MODULES}

    assert names == {"objective_check.py", "counterexample.py",
                     "design_review.py", "arbiter.py"}, names


def test_check_detects_clean_review_side():
    """当前仓库审查侧应为原始状态 —— 这是 B0 能成立的前提（2.1）。"""
    result = bl.check_review_side_clean()

    assert result.get("clean") is True, (
        f"审查侧已被改造，基线的对应维度已污染: {result}")
    assert result.get("contaminated_dimensions") == [], result


def test_check_reports_contamination_when_module_exists(tmp_path, monkeypatch):
    """若某个审查侧模块已落地，必须如实报出污染维度而**不是**放弃采集。

    B0 的 2.1：「若届时 A6-A9 已有部分落地，不要放弃采集 ——
    采一份标注了污染维度的基线，仍远好过没有基线。」
    """
    fake = tmp_path / "sw_lib" / "workflow"
    fake.mkdir(parents=True)
    (fake / "arbiter.py").write_text("# A9 已实施\n", encoding="utf-8")
    monkeypatch.setattr(bl, "ROOT", tmp_path)

    result = bl.check_review_side_clean()

    assert result.get("clean") is False, result
    assert "A9" in result.get("contaminated_dimensions", []), (
        f"arbiter.py 已存在却没报出 A9 污染: {result}")


def test_purity_detail_records_a0_a1_as_already_implemented():
    """A0/A1 已实施构成的污染必须写在归档里（1.3 的定案）。

    严格的「改造前」应在 A0 之前，那个时点已经错过。
    如实标注，A11 引用时才能表述为「审查侧改造的提升」
    而不是「全套设计的提升」。
    """
    detail = bl.check_review_side_clean().get("purity_detail", {})

    assert detail.get("implemented_before_capture") == ["A0", "A1", "A2"], detail
    assert set(detail.get("review_side_untouched", [])) == {"A6", "A7", "A8", "A9"}, detail
    assert detail.get("note"), "必须写明该基线只对『审查能力』这一观测量有效"


def test_purity_label_is_review_side_clean():
    """`baseline_purity` 的取值不得是「完整改造前基线」（1.3）。"""
    assert bl.check_review_side_clean().get("baseline_purity") == \
        "review_side_clean"


# ── 验收 2：git 指纹如实记录 ──

def test_env_fingerprint_records_real_git_sha():
    """git_sha 必须是真实 HEAD，不能编。"""
    expected = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True).stdout.strip()

    assert bl.env_fingerprint().get("git_sha") == expected


def test_env_fingerprint_records_dirty_state_truthfully():
    """dirty 与否都要如实记，且与 `git status` 的实测结果一致。

    只记 sha 会让人误以为基线对应一个干净的提交，事后无法复现那个状态
    （2.3 的定案）。
    """
    porcelain = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                               capture_output=True, text=True).stdout.strip()
    env = bl.env_fingerprint()

    assert env.get("git_dirty") is bool(porcelain), (
        f"git_dirty 与实际不符: dirty={env.get('git_dirty')}, "
        f"porcelain 非空={bool(porcelain)}")
    if porcelain:
        assert env.get("git_status_summary"), "dirty 时必须给出摘要"


def test_env_fingerprint_snapshots_review_stage_roles():
    """对照组最重要的配置特征：04-review 是**单角色**（2.2）。"""
    env = bl.env_fingerprint()

    assert env.get("review_stage_roles") == {"04-review": "reviewer"}, env


def test_env_fingerprint_snapshots_config():
    """五个角色的 agent/model 须原样归档（2.2）——

    A4 的 U4-3 把「独立性」的证明推给了 A11，而这份快照是
    「改造前五角色同模型」这一事实的唯一留证。
    """
    snapshot = bl.env_fingerprint().get("config_snapshot", {})

    assert snapshot, "config_snapshot 不能为空"
    roles = snapshot.get("roles", {})
    assert len(roles) >= 5, f"五个角色都要记: {roles}"
    for name, spec in roles.items():
        assert spec.get("agent"), f"{name} 缺 agent"
        assert spec.get("model"), f"{name} 缺 model"


def test_env_fingerprint_mode_is_real_outside_mock():
    """`mode` 必须如实反映是否 mock —— mock 下采到的是夹具而非能力（第 4 节）。"""
    from sw_lib.core.config import is_mock_agent

    expected = "mock" if is_mock_agent() else "real"
    assert bl.env_fingerprint().get("mode") == expected
