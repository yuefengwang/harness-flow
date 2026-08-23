"""推进路由必须与校验读同一个状态源。

发现于 JSON 状态源重构：`check_stage_compliance` 已改读 `.state`，而
`WorkflowRuntime.advance` 仍用 `parse_route_field` 去 04-review.md 里
正则搜 ``**Route**: `xxx` ``。两个源可以给出不同答案：

* 校验说「Route 决策未填写」→ 拦住；但只要 agent 在正文里写过一句
  ``**Route**: `03-Coding` ``，一旦校验被绕过（或该检查被跳过），
  推进就会按 agent 写的那个目标走。
* 用户在 TUI 里选了归档、写进 `.state`，agent 正文里却复述着返工目标 ——
  `parse_route_field` 的无锚点 `re.search` 取到的是文件里**第一个**匹配，
  可能正是 agent 那句。

推进是不可逆动作（会改 stage_idx、注入返工上下文、重置门禁），因此它必须
只信 `.state`。
"""
import pytest

from sw_lib.core.config import STAGES, TASKS, TPLS
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.runtime import WorkflowRuntime

_REVIEW_IDX = STAGES.index("04-review")


@pytest.fixture
def task(make_task):
    name = make_task("pytest-advance-route-source", stage="04-review",
                     stage_idx=_REVIEW_IDX)
    d = TASKS / name
    (d / "04-review.md").write_text(
        (TPLS / "04-review.md").read_text(encoding="utf-8"), encoding="utf-8")
    return name


def _advance(name):
    return WorkflowRuntime.advance(name)


def test_route_comes_from_state_not_markdown(task):
    """`.state` 说归档，agent 正文里写返工 —— 必须归档。"""
    ss.write_route(task, "05-Archive")

    path = TASKS / task / "04-review.md"
    path.write_text(
        "## 🤖 AI Output\n我建议 **Route**: `03-Coding` 返工。\n" +
        path.read_text(encoding="utf-8"),
        encoding="utf-8")

    st = _advance(task)
    assert st["stage"] == "05-archive", \
        f"推进采纳了 agent 正文里的路由: {st['stage']}"


def test_markdown_route_alone_does_not_reroute(task):
    """`.state` 里没有决策时，agent 正文里的 Route 不得触发返工。
    
    没有决策就只能线性推进 —— 返工是用户的决定，不是 agent 能自己发起的。
    """
    path = TASKS / task / "04-review.md"
    path.write_text("**Route**: `02-Planning`\n", encoding="utf-8")

    st = _advance(task)
    assert st["stage"] == "05-archive", \
        f"agent 正文单方面触发了返工: {st['stage']}"


def test_state_route_reroutes(task):
    """用户真的在 `.state` 里选了返工 —— 必须返工。"""
    ss.write_route(task, "03-Coding")

    st = _advance(task)
    assert st["stage"] == "03-coding"
    assert st["stage_idx"] == STAGES.index("03-coding")


def test_reroute_resets_target_gate(task):
    """返工到的阶段，其门禁签署必须被撤销 —— 否则旧签名会让它直接过。"""
    ss.sign_gate(task, "03-coding")
    assert ss.read_gate(task, "03-coding").signed is True

    ss.write_route(task, "03-Coding")
    _advance(task)

    assert ss.read_gate(task, "03-coding").signed is False, \
        "返工后目标阶段仍带着上一轮的签署"


def test_linear_advance_resets_next_gate(make_task):
    """线性推进同理：下一阶段不能带着历史签名。"""
    name = make_task("pytest-advance-linear-gate", stage="02-planning",
                     stage_idx=1)
    ss.sign_gate(name, "03-coding")

    st = WorkflowRuntime.advance(name)

    assert st["stage"] == "03-coding"
    assert ss.read_gate(name, "03-coding").signed is False


def test_advance_does_not_clobber_sibling_stage_state(task):
    """推进不得覆盖其他阶段的状态。

    `advance` 在入口读一次 `.state`、在末尾写回去。中途 `reset_gate` 等操作
    自己也会写盘，若末尾直接写回入口那份旧快照，这些写入就被静默丢掉 ——
    典型的读-改-写丢失。
    """
    ss.sign_gate(task, "01-brainstorming")
    ss.sign_gate(task, "02-planning")
    ss.write_route(task, "05-Archive")

    WorkflowRuntime.advance(task)

    assert ss.read_gate(task, "01-brainstorming").signed is True, \
        "已完成阶段的签署被推进覆盖掉了"
    assert ss.read_gate(task, "02-planning").signed is True
    # 推进后的当前阶段（归档）自然应是未签署
    assert ss.read_gate(task, "05-archive").signed is False
