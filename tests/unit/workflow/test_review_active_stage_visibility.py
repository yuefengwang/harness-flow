"""A5 的 R2：多审查者并行时 TUI 的单 agent 假设会漏显示。

实测（A5 的 11.1）：并行跑 adversary 与 design_critic 时，从 TUI 的视角
（`adapter.active_stage`）只观测到其中**一个**角色，另一个全程不可见 ——
因为 `create_stage_node` 里 `adapter.active_stage = stage_runnable` 是
后写覆盖前写。

这不影响正确性（产出各自落盘、findings 条数正确），但会让用户以为只跑了
一个审查者。所以要求：多审查者场景下必须能查到**全部**在跑的分支，
而不是只有最后一个赢家。
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


def test_active_roles_tracks_all_parallel_branches():
    """注册两个角色后，两个都必须能被查到 —— 不接受「只剩最后一个」。"""
    RG.reset_active_roles()
    RG.register_active_role("adversary")
    RG.register_active_role("design_critic")

    assert RG.active_roles() == {"adversary", "design_critic"}


def test_finished_role_is_removed():
    RG.reset_active_roles()
    RG.register_active_role("adversary")
    RG.register_active_role("design_critic")
    RG.unregister_active_role("adversary")

    assert RG.active_roles() == {"design_critic"}


def test_single_role_path_reports_nothing_extra():
    """单角色路径不该在这里留下痕迹（向后兼容，验收 7）。"""
    RG.reset_active_roles()
    RG.register_active_role(None)

    assert RG.active_roles() == set()


def test_r2_limitation_is_documented_not_silent():
    """R2 是已知限制，必须能被程序查到，而不是只写在文档里。

    A10 的报告需要据此标注「TUI 显示的审查者数量可能少于实际」。
    """
    status = RG.ui_multi_agent_status()
    assert status["single_agent_assumption"] is True
    assert status["known_risk"] == "R2"
