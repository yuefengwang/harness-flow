"""阶段启动时必须把门禁定义播种进 `.state`，并渲染出可读的 Gate 区。

`read_gate` 在没有记录时会回退模板定义，所以判定本身不依赖播种。但状态源
要成为**事实**，就得在阶段启动时真的落盘一份：这样 `.state` 是自描述的，
外部工具（hook、web、`sw state get`）不必反过来去猜模板长什么样。

对应的旧行为是 `ensure_gate_section` —— 往 Markdown 里补一个 `## Gate` 区。
那是把 Markdown 当状态源时代的自愈手段，现在应由播种 + 单向渲染取代。
"""
import pytest

from sw_lib.core.config import STAGES, TASKS, TPLS
from sw_lib.core.state import read_state
from sw_lib.workflow import stage_state as ss

_STAGE = "02-planning"


@pytest.fixture
def task(make_task):
    name = make_task("pytest-gate-seeding", stage=_STAGE, stage_idx=1)
    d = TASKS / name
    (d / f"{_STAGE}.md").write_text(
        (TPLS / f"{_STAGE}.md").read_text(encoding="utf-8"), encoding="utf-8")
    return name, d


def test_seed_writes_items_to_state(task):
    name, _ = task
    assert ss.seed_gate(name, _STAGE) is True

    gate = read_state(name)["stages"][_STAGE]["gate"]
    assert gate["items"], "门禁项未落盘"
    assert all(i["checked"] is False for i in gate["items"])
    assert gate["signed_by"] is None


@pytest.mark.parametrize("stage", STAGES)
def test_every_stage_can_be_seeded(make_task, stage):
    """每个阶段都要能播种，否则该阶段的 .state 不自描述。"""
    name = make_task(f"pytest-seed-{stage}", stage=stage)
    assert ss.seed_gate(name, stage) is True
    items = read_state(name)["stages"][stage]["gate"]["items"]
    assert items, f"{stage}: 模板门禁项为空"


def test_seed_does_not_clobber_existing_signature(task):
    """已签署的阶段重新启动（返工）时，播种不得把签名冲掉。"""
    name, _ = task
    ss.sign_gate(name, _STAGE)
    assert ss.seed_gate(name, _STAGE) is False

    gate = ss.read_gate(name, _STAGE)
    assert gate.signed is True
    assert gate.signed_by == "user"


def test_render_reflects_signature_in_markdown(task):
    """签署后渲染出的 Gate 区应是勾上的 —— 人读文件能看出状态。"""
    name, d = task
    ss.seed_gate(name, _STAGE)
    ss.sign_gate(name, _STAGE)
    assert ss.render_gate_section(name, _STAGE) is True

    content = (d / f"{_STAGE}.md").read_text(encoding="utf-8")
    body = content[content.rfind("## Gate"):]
    assert "- [ ]" not in body, f"已签署却渲染出未勾选项:\n{body}"
    assert "- [x]" in body


def test_render_is_idempotent(task):
    """重复渲染不得增殖 Gate 区。"""
    name, d = task
    ss.seed_gate(name, _STAGE)
    ss.render_gate_section(name, _STAGE)
    first = (d / f"{_STAGE}.md").read_text(encoding="utf-8")
    assert ss.render_gate_section(name, _STAGE) is False, "无变化时不该重写文件"
    assert (d / f"{_STAGE}.md").read_text(encoding="utf-8") == first
    assert first.count("## Gate") == 1


def test_render_is_one_way_only(task):
    """手改 Markdown 的复选框不影响判定 —— 文件是映像，不是状态源。"""
    name, d = task
    ss.seed_gate(name, _STAGE)
    path = d / f"{_STAGE}.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
        encoding="utf-8")

    assert ss.read_gate(name, _STAGE).signed is False, "手改文件骗过了门禁"
    # 再渲染一次，文件被拉回状态的映像
    ss.render_gate_section(name, _STAGE)
    body = path.read_text(encoding="utf-8")
    gate_body = body[body.rfind("## Gate"):]
    assert "- [x]" not in gate_body, "渲染没有覆盖手改的勾选"


def test_stage_start_seeds_gate(task):
    """阶段启动路径必须完成播种，用户不必先做任何动作。"""
    from unittest.mock import MagicMock
    from sw_lib.workflow.base import StageRunnable

    name, _ = task
    st = read_state(name)
    assert "stages" not in st or _STAGE not in st.get("stages", {})

    r = StageRunnable(_STAGE, 1, MagicMock(), MagicMock(), MagicMock(),
                      MagicMock())
    r._seed_stage_gate(name)

    gate = read_state(name).get("stages", {}).get(_STAGE, {}).get("gate")
    assert gate and gate["items"], "阶段启动后 .state 里没有门禁定义"


def test_invoke_seeds_gate_before_agent_runs(task):
    """播种必须发生在 invoke 的准备段 —— 用户可能在 agent 说完前就按 [A]。"""
    import inspect
    from sw_lib.workflow.base import StageRunnable

    src = inspect.getsource(StageRunnable.invoke)
    seed_at = src.find("_seed_stage_gate")
    agent_at = src.find("_run_agent")
    assert seed_at >= 0, "invoke 未播种门禁定义"
    assert agent_at < 0 or seed_at < agent_at, "播种晚于 agent 启动"
