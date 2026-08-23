"""A5 的 R2：多审查者并行时显示层不得只显示其中一个。

实测过：`adapter.active_stage` 是单值，后写覆盖前写，并行两个审查者时
TUI 只能看到一个（A5 的 11.1）。A5 交付了 `review_graph.active_roles()`
提供完整列表，但**没有接进渲染代码** —— 于是 R2 只是「可查」不是「已显示」。

判据：并行 N 个审查者时，界面标题必须体现出 N 个都在跑。
只显示一个会让用户以为审查覆盖面比实际窄，或反过来以为已跑够。
"""

import pytest

from sw_lib.workflow import review_graph as RG


@pytest.fixture(autouse=True)
def _clean_roles():
    RG.reset_active_roles()
    yield
    RG.reset_active_roles()


def test_label_lists_all_active_reviewers():
    RG.register_active_role("adversary")
    RG.register_active_role("design_critic")

    label = RG.active_roles_label()

    assert "adversary" in label
    assert "design_critic" in label, f"只显示了一个审查者：{label!r}"


def test_label_shows_count_when_multiple():
    """带数量，让「跑了几个」一眼可见。"""
    RG.register_active_role("a")
    RG.register_active_role("b")
    RG.register_active_role("c")

    assert "3" in RG.active_roles_label()


def test_label_empty_when_no_parallel_reviewers():
    """单角色路径不该多出显示内容（向后兼容）。"""
    assert RG.active_roles_label() == ""


def test_label_is_stable_order():
    """顺序必须稳定，否则界面每帧抖动。"""
    RG.register_active_role("zebra")
    RG.register_active_role("alpha")

    assert RG.active_roles_label() == RG.active_roles_label()
    assert RG.active_roles_label().index("alpha") < \
        RG.active_roles_label().index("zebra")


def test_tui_header_includes_reviewer_label():
    """接线：TUI 的标题渲染必须真的用上这个标签。

    只测 active_roles_label() 本身等于没测 —— A5 已经交付了 active_roles()，
    缺的正是「有人调用」。
    """
    import inspect

    from sw_lib.ui import tui

    src = inspect.getsource(tui)
    assert "active_roles_label" in src, \
        "TUI 没有引用 active_roles_label()，R2 仍然只是「可查」不是「已显示」"
