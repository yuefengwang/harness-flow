"""04-review：渲染层与分发层必须对同一个输入达成一致。

现场 bug（任务 oooo）：Route 已经是 `05-Archive`，面板据此显示
「[A] 批准评审 — 勾选 Gate」，但 `_dispatch` 的路由分支只判断
``stage == "04-review" and input_mode == "options"``，不看 Route 是否已填，
于是把 A 当成路由选择、把同一个值重复写一遍：

    user  [A] 返工到 归档 (05-Archive)
    sw    ✓ Route 已设置为 05-Archive。输入 /advance 推进。
    user  [A] 返工到 归档 (05-Archive)      ← 用户反复按都是这个
    sw    ✓ Route 已设置为 05-Archive。

Gate 因此永远签不上，/advance 一直报「4 个待填项未完成」——用户无从完成。
"""
import queue
import shutil
from unittest.mock import patch

import pytest

from sw_lib.core.config import STAGES, TASKS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.ui.tui import MonitorTUI, TUIState

_TASK = "pytest-review-dispatch"


@pytest.fixture
def task_dir():
    d = TASKS / _TASK
    d.mkdir(parents=True, exist_ok=True)
    # 门禁判定读 .state（docs/design-json-state-source.md），必须有状态文件
    write_state(_TASK, {"id": _TASK, "stage": "01-brainstorming",
                        "stage_idx": 0, "stage_status": "running"})
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _tui(stage="04-review"):
    tui = MonitorTUI.__new__(MonitorTUI)
    # 用真实的 TUIState 而不是 MagicMock：mock 上未设置的属性是真值
    # (bool(m.pending_questions) is True)，会让 _dispatch 的前置分支恒真、
    # 后面的路由/签署分支永远不可达 —— 测试会"通过"却什么都没验证。
    tui.state = TUIState(name=_TASK, stage=stage,
                         stage_idx=STAGES.index(stage))
    tui.state.input_mode = "options"
    tui.state.log_lines = [("agent", "评审完成")]
    tui.cmd_queue = queue.Queue()
    tui.callbacks = {}
    tui._auto_answer = False
    tui.logs = []
    tui._add_log = lambda src, msg: tui.logs.append((src, msg))
    tui._refresh_display = lambda: None
    return tui


# agent 遵守编排规则（不改文件推进阶段），把结论写在 AI Output 正文里，
# 模板真正的 ## Gate 仍然全空 —— 这就是现场文件的形状。
_ROUTE_FILLED = """# 04-Review

## Security
- [ ] No hardcoded secrets

## Review Decision
- **Route**: `05-Archive` (05-Archive / 03-Coding / 02-Planning)
- **Reason**: 所有门禁通过

## 🤖 AI Output
根据编排规则，我不应修改文件来推进阶段。以下是评审结论：

### Gate
- [x] Full build: pytest 5/5 pass
- [x] Lint/static analysis: 无 lint 配置

## Gate
- [ ] Full build: `___`
- [ ] Lint/static analysis pass
- [ ] Doc/config in sync
- [ ] README.md CLI commands verified
"""


def test_route_already_filled_routes_a_to_signoff(task_dir):
    """Route 已填 → A 必须走签署，而不是重复写 Route。"""
    (task_dir / "04-review.md").write_text(_ROUTE_FILLED, encoding="utf-8")
    ss.write_route(_TASK, "05-Archive")   # 决策存 .state，不是 Markdown
    tui = _tui()

    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor"):
        tui._dispatch("A")

    msgs = [m for _, m in tui.logs]
    assert not any("Route 已设置" in m for m in msgs), \
        f"Route 已填却又走了路由分支: {msgs}"
    assert any("Gate 已签署" in m for m in msgs), f"A 未触发签署: {msgs}"


def test_signoff_records_state_and_renders_markdown(task_dir):
    """签署写状态，并把 Gate 区渲染成状态的映像。

    旧实现要在文件里找对 ``## Gate`` 才能勾对地方，会被 AI Output 里的
    ``### Gate`` 干扰。现在判定与文件解耦，渲染只是显示。
    """
    (task_dir / "04-review.md").write_text(_ROUTE_FILLED, encoding="utf-8")
    ss.write_route(_TASK, "05-Archive")   # 决策存 .state，不是 Markdown
    tui = _tui()

    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor"):
        tui._dispatch("A")

    assert ss.read_gate(_TASK, "04-review").signed is True, "状态未记录签署"
    content = (task_dir / "04-review.md").read_text(encoding="utf-8")
    gate = content[content.rfind("\n## Gate"):]
    assert "[ ]" not in gate, f"渲染后的 Gate 区仍有未勾项: {gate!r}"


def test_signoff_unblocks_validation(task_dir):
    """签署后软校验必须放行 —— 这是「用户能完成」的判定标准。"""
    from sw_lib.workflow.utils import check_stage_compliance

    (task_dir / "04-review.md").write_text(_ROUTE_FILLED, encoding="utf-8")
    ss.write_route(_TASK, "05-Archive")   # 决策存 .state，不是 Markdown
    tui = _tui()

    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor"):
        tui._dispatch("A")

    _, todo = check_stage_compliance(_TASK, "04-review", 3)
    assert todo == [], f"签署后仍被拦住: {todo}"


def test_route_unfilled_still_routes(task_dir):
    """Route 未填时 A/B/C 仍必须是路由选择 —— 不能被签署入口顶掉。"""
    # .state 里没有 route 记录即为「未决策」；Markdown 写什么都不影响
    (task_dir / "04-review.md").write_text(_ROUTE_FILLED, encoding="utf-8")
    ss.reset_route(_TASK)
    tui = _tui()

    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor"):
        tui._dispatch("B")

    msgs = [m for _, m in tui.logs]
    assert any("Route 已设置为 03-Coding" in m for m in msgs), \
        f"Route 未填时 B 应选路由到 03-Coding: {msgs}"


def test_repeated_a_does_not_rewrite_same_route(task_dir):
    """连按两次 A：第二次不该再报一遍「Route 已设置」。"""
    # .state 里没有 route 记录即为「未决策」；Markdown 写什么都不影响
    (task_dir / "04-review.md").write_text(_ROUTE_FILLED, encoding="utf-8")
    ss.reset_route(_TASK)
    tui = _tui()

    with patch("sw_lib.workflow.runtime.WorkflowRuntime.get_executor"):
        tui._dispatch("A")   # 第一次：填 Route
        tui.state.input_mode = "options"  # 面板重新给出签署选项
        tui._dispatch("A")   # 第二次：应当签署

    msgs = [m for _, m in tui.logs]
    assert sum("Route 已设置" in m for m in msgs) == 1, \
        f"Route 被重复写入: {msgs}"
    assert any("Gate 已签署" in m for m in msgs), \
        f"第二次 A 未推进到签署: {msgs}"
