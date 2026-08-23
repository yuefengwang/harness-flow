"""A5 的 R3：MockAgent 必须按 role 返回不同产出。

多审查者并行时若每个 role 的 mock 产出完全相同，那么「两个审查者」与
「一个审查者跑两遍」在产出上不可区分 —— 基于 mock 的多轨测试就失去了意义，
且真正的串味 bug（分支共用缓冲导致产出互相覆盖）也测不出来。

判据是产出**可区分**，不是产出「非空」。
"""

import pytest


def _mock_for(role_id, stage="04-review", stage_idx=3):
    from sw_lib.agents.mock import MockAgent
    texts = []
    # _say 走 _add_log 落到 add_log 回调，且 running 为 false 时直接返回 ——
    # 不置位就收不到任何产出，测试会误判成「没有角色标记」。
    cb = {"add_log": lambda s, m: texts.append(m), "is_running": lambda: True}
    a = MockAgent(cb, f"rw-mockrole-{role_id or 'none'}", stage, stage_idx,
                  role_id=role_id)
    a.running = True
    return a, texts


def test_different_roles_produce_distinguishable_output():
    """两个角色的产出必须能被区分开。"""
    a1, _ = _mock_for("adversary")
    a2, _ = _mock_for("design_critic")

    out1 = a1.role_flavored_output("基础产出")
    out2 = a2.role_flavored_output("基础产出")

    assert out1 != out2, "两个角色的 mock 产出完全相同，多轨测试无意义"
    assert "adversary" in out1
    assert "design_critic" in out2


def test_no_role_output_unchanged():
    """无 role 时产出必须与改造前一致（向后兼容）。

    单角色路径的既有 e2e 断言依赖具体文本，加装饰会把它们全弄红。
    """
    a, _ = _mock_for(None)
    assert a.role_flavored_output("基础产出") == "基础产出"


def test_same_role_is_deterministic():
    """同一角色必须稳定 —— mock 的价值就在可复现。"""
    a, _ = _mock_for("adversary")
    assert a.role_flavored_output("X") == a.role_flavored_output("X")


def test_role_marker_survives_in_review_scenario():
    """接线：04 审查场景的实际产出里必须带角色标记。

    只测 role_flavored_output() 本身等于没测有人调用它。
    """
    a, texts = _mock_for("adversary")
    a._scenario_review()
    blob = "".join(texts)
    assert "adversary" in blob, f"审查场景产出未体现角色：{blob[:200]!r}"
