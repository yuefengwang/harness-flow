"""A4 的验收 9：`role_id=None` 时解析结果逐字节等价。

这是一条**回归防护测试**，不是功能测试 —— 它在改造之前就应当是绿的
（A4 的 9.3）。若快照采集本身有误，改造后它会红，而人会误以为是改造
破坏了兼容性，然后去改实现 —— 方向完全错了。所以：

    先确认本文件在改造前为绿，再动 config.py。

快照于改造前在未污染进程内实测采集（见 A4 的 9.3），15 个值 =
5 个 stage × 3 个解析函数。
"""

import copy

import pytest

from sw_lib.core import config as C


# 改造前实测采集，逐值写死。不得在改造后重新生成。
BASELINE = {
    "01-brainstorming": {
        "type": "opencode",
        "model": "opencode/mimo-v2.5-free",
        "tools": ["list_files", "read_file", "write_file", "ask_user"],
    },
    "02-planning": {
        "type": "opencode",
        "model": "opencode/mimo-v2.5-free",
        "tools": ["list_files", "read_file", "write_file", "ask_user"],
    },
    "03-coding": {
        "type": "opencode",
        "model": "opencode/mimo-v2.5-free",
        "tools": ["list_files", "read_file", "write_file", "run_command", "ask_user"],
    },
    "04-review": {
        "type": "opencode",
        "model": "opencode/mimo-v2.5-free",
        "tools": ["list_files", "read_file", "run_command", "ask_user"],
    },
    "05-archive": {
        "type": "opencode",
        "model": "opencode/mimo-v2.5-free",
        "tools": ["list_files", "read_file", "write_file", "ask_user"],
    },
}


@pytest.fixture(autouse=True)
def _restore_global_config():
    """还原模块级全局单例的配置（A4 的 9.1）。

    `_manager.config` 是进程级单例，`roles[x].model = y` 改的是嵌套
    dataclass 实例的字段，浅拷贝还原不了 —— 必须 deepcopy。
    """
    snapshot = copy.deepcopy(C._manager.config)
    yield
    C._manager._config = snapshot


@pytest.mark.parametrize("stage", sorted(BASELINE))
def test_resolve_agent_type_matches_pre_change_baseline(stage):
    assert C.resolve_agent_type(stage) == BASELINE[stage]["type"]


@pytest.mark.parametrize("stage", sorted(BASELINE))
def test_resolve_agent_model_matches_pre_change_baseline(stage):
    assert C.resolve_agent_model(stage) == BASELINE[stage]["model"]


@pytest.mark.parametrize("stage", sorted(BASELINE))
def test_get_tools_for_stage_matches_pre_change_baseline(stage):
    assert C.get_tools_for_stage(stage) == BASELINE[stage]["tools"]


@pytest.mark.parametrize("stage", sorted(BASELINE))
def test_explicit_none_role_id_equals_omitted(stage):
    """显式传 `role_id=None` 与不传必须等价。

    改造后三个函数会多出可选参数。这条守住「默认值不改变行为」——
    只跑 `resolve_agent_type(stage)` 无法发现新参数的默认值写错了
    （比如默认成了 stage_roles 之外的角色）。
    """
    try:
        got_type = C.resolve_agent_type(stage, None, role_id=None)
        got_model = C.resolve_agent_model(stage, None, role_id=None)
        got_tools = C.get_tools_for_stage(stage, role_id=None)
    except TypeError:
        pytest.skip("role_id 参数尚未引入（改造前）")
    assert got_type == BASELINE[stage]["type"]
    assert got_model == BASELINE[stage]["model"]
    assert got_tools == BASELINE[stage]["tools"]
