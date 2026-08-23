"""/advance 与 TUI 输入分发的鲁棒性。

主流程审视发现的问题，共同特征是「用户无从察觉、也无从补救」：
写文件静默失败仍报成功、输入落进死路、无效输入毫无反馈。
"""
import queue
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from sw_lib.core.config import STAGES, TASKS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.ui.tui import MonitorTUI, TUIState

_TASK = "pytest-advance-robust"


@pytest.fixture
def task_dir():
    d = TASKS / _TASK
    d.mkdir(parents=True, exist_ok=True)
    # 门禁判定读 .state（docs/design-json-state-source.md），必须有状态文件
    write_state(_TASK, {"id": _TASK, "stage": "01-brainstorming",
                        "stage_idx": 0, "stage_status": "running"})
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _tui(stage="01-brainstorming"):
    tui = MonitorTUI.__new__(MonitorTUI)
    # 用真实的 TUIState 而不是 MagicMock：mock 上未设置的属性是真值
    # (bool(m.pending_questions) is True)，会让 _dispatch 的前置分支恒真、
    # 后面的路由/签署分支永远不可达 —— 测试会"通过"却什么都没验证。
    tui.state = TUIState(name=_TASK, stage=stage,
                         stage_idx=STAGES.index(stage))
    tui.state.input_mode = "options"
    tui.state.options = [("A", "A. 批准"), ("B", "B. 修订")]
    tui.cmd_queue = queue.Queue()
    tui.callbacks = {}
    tui._auto_answer = False
    tui.logs = []
    tui._add_log = lambda src, msg: tui.logs.append((src, msg))
    tui._refresh_display = lambda: None
    return tui


# ── 1. Route 写入静默失败 ──

def test_write_review_route_persists_decision(task_dir):
    """路由决策写进 .state，而不是改 Markdown 的 Route 行。

    旧实现在文件里正则替换 `- **Route**: xxx`，于是 agent 把该行写成别的
    格式（`待定`、整行缺失、或在 AI Output 里再写一个 Route）都会让替换
    静默失效或命中错误的那一行。决策进 JSON 之后这类失败模式整体消失。
    """
    (task_dir / "04-review.md").write_text(
        "# 04-Review\n\n## Review Decision\n- **Reason**: x\n", encoding="utf-8")
    tui = _tui("04-review")

    assert tui._write_review_route("03-Coding") is True
    assert ss.read_route(_TASK) == "03-coding"


def test_write_review_route_rejects_invalid_target(task_dir):
    """非法目标必须如实失败，不能静默记下一个无效阶段名。"""
    (task_dir / "04-review.md").write_text("# 04-Review\n", encoding="utf-8")
    tui = _tui("04-review")

    assert tui._write_review_route("不存在的阶段") is False
    assert ss.read_route(_TASK) is None


def test_write_review_route_works_without_markdown(task_dir):
    """阶段文件缺失也能记录决策 —— 状态不寄生于 Markdown。"""
    md = task_dir / "04-review.md"
    if md.exists():
        md.unlink()
    tui = _tui("04-review")

    assert tui._write_review_route("05-Archive") is True
    assert ss.read_route(_TASK) == "05-archive"


def test_write_review_route_missing_task_returns_false():
    """任务不存在（无 .state）时如实失败。"""
    tui = _tui("04-review")
    tui.state.name = "pytest-nonexistent-task"
    assert tui._write_review_route("05-Archive") is False


def test_reroute_fills_evidence_table_for_humans(task_dir):
    """返工时把 Evidence 表填好供人阅读 —— 那是给人看的证据，不是判定依据。

    内容取自 `.state` 的拍板记录。这里没有记录，因此走兜底文案 ——
    兜底也必须指向能查到理由的地方，不能是「详见审查结论」那种既像内容
    又没内容的话（任务 T3：agent 拿到这种描述连续三轮不知道要补什么）。
    """
    (task_dir / "04-review.md").write_text(
        "# 04-Review\n\n### Reroute Evidence\n"
        "| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |\n"
        "|---|------|---------|---------|-------------|\n"
        "| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n"
        "| 2 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n",
        encoding="utf-8")
    tui = _tui("04-review")

    assert tui._write_review_route("03-Coding") is True
    content = (task_dir / "04-review.md").read_text(encoding="utf-8")
    row = [ln for ln in content.splitlines() if ln.startswith("| 1 |")]
    assert row, "返工证据表未回填"
    assert "___" not in row[0], f"占位符仍在: {row[0]}"
    assert "04-review 对话记录" in row[0], row[0]
    assert "coding" in content


def test_reroute_evidence_uses_recorded_decision(task_dir):
    """有拍板记录时必须用用户的原话，而不是任何模板文案。"""
    (task_dir / "04-review.md").write_text(
        "# 04-Review\n\n### Reroute Evidence\n"
        "| # | 问题 | 严重程度 | 归属阶段 | 具体位置/描述 |\n"
        "|---|------|---------|---------|-------------|\n"
        "| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n"
        "| 2 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n",
        encoding="utf-8")
    ss.record_decision(_TASK, "04-review",
                       question="缺少 README.md，如何处理？",
                       answer="返工到 03-Coding 补文档")
    tui = _tui("04-review")

    assert tui._write_review_route("03-Coding") is True
    content = (task_dir / "04-review.md").read_text(encoding="utf-8")
    assert "README.md" in content, "用户给的真实理由没进 Evidence 表"


def test_archive_route_leaves_evidence_table_alone(task_dir):
    """归档不是返工，不该填返工证据。"""
    (task_dir / "04-review.md").write_text(
        "# 04-Review\n\n### Reroute Evidence\n"
        "| 1 | ___ | high/med/low | coding/planning/brainstorming | ___ |\n",
        encoding="utf-8")
    tui = _tui("04-review")

    assert tui._write_review_route("05-Archive") is True
    content = (task_dir / "04-review.md").read_text(encoding="utf-8")
    assert "| 1 | ___ |" in content, "归档路由不该动 Evidence 表"


# ── 2. A/B 选项不匹配时穿透成 agent 回复 ──

def test_invalid_design_choice_does_not_fall_through(task_dir):
    """agent 已 idle，回复无人接收；必须提示重选而不是把输入丢进 executor。"""
    (task_dir / "01-brainstorming.md").write_text(
        "# 01\n## Gate\n- [ ] Design approved\n", encoding="utf-8")
    tui = _tui("01-brainstorming")

    with patch.object(MonitorTUI, "_is_gate_signoff_needed", return_value=True), \
         patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor") as ex:
        tui._dispatch("Z")

    ex.return_value.answer.assert_not_called()
    assert any("A" in msg and "B" in msg for _, msg in tui.logs), \
        f"未提示有效选项: {tui.logs}"


def test_invalid_review_route_choice_does_not_fall_through(task_dir):
    (task_dir / "04-review.md").write_text(
        "# 04\n- **Route**: `___`\n", encoding="utf-8")
    tui = _tui("04-review")

    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor") as ex:
        tui._dispatch("Z")

    ex.return_value.answer.assert_not_called()


# ── 3. 结算阶段无效输入毫无反馈 ──

def test_settlement_invalid_choice_gives_feedback():
    tui = _tui("05-archive")
    tui.state.is_settled = True
    tui.running = True

    tui._handle_settlement_choice("Z")

    assert tui.logs, "无效输入没有任何反馈，用户会以为界面卡死"
    assert tui.running is True, "无效输入不应退出面板"


# ── 4. Gate 勾选不得越界改动正文 ──

def test_design_approval_only_touches_gate(task_dir):
    """Gate 之前的 `[ ]`（备选方案、待办）不能被一起勾上。"""
    (task_dir / "01-brainstorming.md").write_text(
        "# 01-Brainstorming\n\n"
        "## Clarifying Questions\n"
        "1. **Topic**: x\n"
        "   - [ ] A: 方案甲\n"
        "   - [ ] B: 方案乙\n"
        "\n## Gate\n"
        "- [ ] Design approved\n"
        "- [ ] Ready for Planning\n",
        encoding="utf-8")
    tui = _tui("01-brainstorming")

    assert tui._write_gate_signoff() is True
    content = (task_dir / "01-brainstorming.md").read_text(encoding="utf-8")
    head = content.split("## Gate")[0]
    assert "[ ] A: 方案甲" in head, "Gate 之外的复选框被误勾"
    gate = content.split("## Gate")[1]
    assert "[ ]" not in gate


# ── 5. Chosen 占位类值不算已选择 ──

@pytest.mark.parametrize("val", ["___", "N/A", "TBD", "待定", "-", "?"])
def test_placeholder_chosen_values_do_not_resolve(val):
    from sw_lib.workflow.utils import _resolves_choice_group
    assert _resolves_choice_group(f"   - **Chosen**: {val}") is False, \
        f"{val!r} 是未决占位，不应视为已选择"


@pytest.mark.parametrize("val", ["B2B批发平台", "React + FastAPI", "轻量询价模式"])
def test_real_chosen_values_resolve(val):
    from sw_lib.workflow.utils import _resolves_choice_group
    assert _resolves_choice_group(f"   - **Chosen**: {val}") is True


# ── 6. 硬校验失败原因必须回显 ──

def test_hook_failure_output_is_surfaced(task_dir):
    """看不到 hook 的输出，用户根本不知道要改什么。"""
    (task_dir / "01-brainstorming.md").write_text(
        "# 01\n## Gate\n- [x] Design approved\n", encoding="utf-8")
    tui = _tui("01-brainstorming")
    executor = MagicMock(active_stage=None)

    fake = subprocess.CompletedProcess(
        args=[], returncode=1,
        stdout="❌ 缺少 Task DAG\n", stderr="")

    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor",
               return_value=executor), \
         patch("sw_lib.core.service._service") as svc, \
         patch("sw_lib.ui.tui.subprocess.run", return_value=fake):
        svc.get_task_state.return_value = {"stage_idx": 0, "stage_status": "running"}
        svc.validate_stage.return_value = ([], [])
        result = tui._run_advance(auto=False)

    assert result == "blocked"
    assert any("缺少 Task DAG" in msg for _, msg in tui.logs), \
        f"hook 失败原因未回显: {tui.logs}"
