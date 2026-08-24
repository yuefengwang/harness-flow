"""门禁必须可达：每个阶段的 Gate 都要有人能填。

回归背景：01-brainstorming 的 analyst 角色只有 list_files/read_file，既不能
调 ask_user 提问，也无法回填模板；`## Gate` 里的 `[ ] Design approved` 因此
永远不会被勾选，check_01-brainstorming.sh 会无限拒绝 /advance —— TUI 表现为
反复输入 /advance 都停在 01-brainstorming。
"""
import json
import shutil

import pytest

from sw_lib.core.config import STAGES, TASKS, TPLS, get_tools_for_stage
from sw_lib.ui.tui import MonitorTUI, TUIState


# 提示词强制要求用 ask_user 提问，所以每个阶段都必须真的被授权该工具。
def test_every_stage_can_ask_user():
    for stage in STAGES:
        tools = get_tools_for_stage(stage)
        assert "ask_user" in tools, (
            f"{stage} 未授权 ask_user，但提示词要求必须用它提问"
        )


@pytest.mark.parametrize("stage", ["01-brainstorming", "02-planning", "03-coding", "05-archive"])
def test_writable_stages_can_fill_own_template(stage):
    """需要回填模板占位符的阶段必须有写权限。"""
    assert "write_file" in get_tools_for_stage(stage), (
        f"{stage} 无 write_file，无法回填模板 `___` 与选项复选框"
    )


def test_review_stage_can_write_but_critic_stays_read_only():
    """04-review 有写权限；主观轨的 `design_critic` 仍然纯只读。

    ⚠️ **判据重做**（DEV-PROTOCOL 1.2）。原判据「04 不应获得写权限」与客观轨
    O6 直接矛盾：O6 要求 `repo/<task>/README.md` 存在且非空，而 03 的 prompt
    从不提 README、04 又不能写 —— 任务 `qqqq` 因此在两次 `/advance` 之间原地
    卡死，输出逐字相同（A0 的 2.9.11）。用户拍板给 reviewer `write_file`。

    「审查者不改被审对象」换成硬层的精确边界（判据区仍 deny），见
    `tests/unit/workflow/test_review_readme_deadlock.py`。
    这里只守配置层的两件事：reviewer 能写、design_critic 不能。
    """
    assert "write_file" in get_tools_for_stage("04-review"), \
        "04 无 write_file —— O6 要求的 README 无人能创建"

    from sw_lib.core.config import get_tools_for_role

    critic = get_tools_for_role("design_critic", "04-review")
    assert "write_file" not in critic, \
        f"design_critic 被顺手放开了写权限（A8 的 3.6 纯只读）: {critic}"


def test_role_tools_are_known_names():
    """角色工具名必须是 Toolbox 真实存在的工具，拼错会静默失去权限。"""
    known = {"list_files", "read_file", "write_file", "run_command", "ask_user"}
    from sw_lib.core.config import _manager
    for role_id, role in _manager.config.roles.items():
        unknown = set(role.tools) - known
        assert not unknown, f"角色 {role_id} 配了未知工具: {unknown}"


# ── Gate 勾选（TUI 代填用户批准）──

@pytest.fixture
def brainstorm_task():
    name = "gate-reachable-test"
    task_dir = TASKS / name
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / ".state").write_text(
        json.dumps({"id": name, "stage": "01-brainstorming", "stage_idx": 0}),
        encoding="utf-8",
    )
    tpl = (TPLS / "01-brainstorming.md").read_text(encoding="utf-8")
    # 模拟 agent 已产出内容（_save_stage_output 的落盘形状）
    body, gate = tpl.split("\n## Gate", 1)
    (task_dir / "01-brainstorming.md").write_text(
        body + "\n\n## 🤖 AI Output\n结论：采用方案 A。\n## Gate" + gate,
        encoding="utf-8",
    )
    yield name
    shutil.rmtree(task_dir, ignore_errors=True)


def _FakeTUI(name, stage="01-brainstorming", status="idle"):
    """真实 MonitorTUI + 真实 TUIState，只跳过 Rich 初始化。

    早先这里是个手写替身，只挂了当时用到的几个属性。签署判定后来抽出
    ``_agent_has_produced_output`` 辅助方法，替身没有该方法 → 直接 AttributeError。
    用真实类可以避免这种「生产代码演进、替身悄悄落后」的脆弱。
    """
    tui = MonitorTUI.__new__(MonitorTUI)
    tui.state = TUIState(name=name, stage=stage,
                         stage_idx=STAGES.index(stage))
    tui.state.agent_status = status
    return tui


def test_design_approval_offered_when_gate_unchecked(brainstorm_task):
    assert MonitorTUI._is_gate_signoff_needed(_FakeTUI(brainstorm_task)) is True


def test_design_approval_checks_gate_and_unblocks_hook(brainstorm_task):
    from sw_lib.workflow.utils import check_stage_compliance

    tui = _FakeTUI(brainstorm_task)
    assert MonitorTUI._write_gate_signoff(tui) is True

    content = (TASKS / brainstorm_task / "01-brainstorming.md").read_text(encoding="utf-8")
    gate = content[content.rfind("## Gate"):]
    assert "[ ]" not in gate, "Gate 区应全部勾选"

    _, todo = check_stage_compliance(brainstorm_task, "01-brainstorming", 0)
    assert not any("Design approved" in t for t in todo), \
        f"Design approved 仍被判为待填: {todo}"

    # 勾选后不应再重复提示批准
    assert MonitorTUI._is_gate_signoff_needed(tui) is False


def test_design_approval_preserves_ai_output(brainstorm_task):
    """批准只动 Gate 区，不得篡改 AI Output 正文或模板占位符。"""
    path = TASKS / brainstorm_task / "01-brainstorming.md"
    before = path.read_text(encoding="utf-8")
    MonitorTUI._write_gate_signoff(_FakeTUI(brainstorm_task))
    after = path.read_text(encoding="utf-8")

    assert "结论：采用方案 A。" in after
    head_before = before[:before.rfind("\n## Gate")]
    head_after = after[:after.rfind("\n## Gate")]
    assert head_before == head_after, "Gate 之前的内容不应被修改"


def test_no_approval_prompt_before_agent_output(brainstorm_task):
    """agent 还没产出时不应弹批准选项（避免刚启动就要求批准）。"""
    tpl = (TPLS / "01-brainstorming.md").read_text(encoding="utf-8")
    (TASKS / brainstorm_task / "01-brainstorming.md").write_text(tpl, encoding="utf-8")
    assert MonitorTUI._is_gate_signoff_needed(_FakeTUI(brainstorm_task)) is False


def test_no_approval_prompt_while_agent_active(brainstorm_task):
    tui = _FakeTUI(brainstorm_task, status="active")
    assert MonitorTUI._is_gate_signoff_needed(tui) is False


def test_approval_offered_when_agent_wrote_template_directly(brainstorm_task):
    """agent 用 write_file 直接回填模板时没有 AI Output 标记，也必须提示批准。

    真实 opencode 走的就是这条路径：它 edit 模板本身，不经过 _save_stage_output，
    因此不能只靠 "## 🤖 AI Output" 判断是否已有产出。
    """
    path = TASKS / brainstorm_task / "01-brainstorming.md"
    tpl = (TPLS / "01-brainstorming.md").read_text(encoding="utf-8")
    body, gate = tpl.split("\n## Gate", 1)
    filled = body.replace("___", "已确定内容").replace("- [ ] A:", "- [x] A:")
    path.write_text(filled + "\n## Gate" + gate, encoding="utf-8")

    assert MonitorTUI._is_gate_signoff_needed(_FakeTUI(brainstorm_task)) is True
