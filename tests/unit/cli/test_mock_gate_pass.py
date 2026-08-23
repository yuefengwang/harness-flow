"""`sw test` 的门禁 mock 必须写 .state，不能改 Markdown 复选框。

背景：门禁/Route/拍板记录的判定早已迁到 `.state`（见
docs/design-json-state-source.md），但 `_mock_gate_pass` 长期停留在
「把 Markdown 里的 `[ ]` 换成 `[x]`」的老写法上。那种改法对
`check_stage_compliance` 毫无作用，于是 `sw test` 这条进程内 e2e 路径
每个阶段都会被自己的门禁拦住，等于静默失效。

这里的断言全部走真实校验 `check_stage_compliance`，而不是去看 mock
自己写了什么 —— 只有这样才能在判定逻辑再次搬家时转红。
"""
import shutil

import pytest

from sw_lib.cli.test_cmd import _mock_gate_pass
from sw_lib.core.config import STAGES, TASKS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.utils import check_stage_compliance

CHOICE_MD = """# 01-brainstorming

## 方案选择

- [ ] A: 用 SQLite
- [ ] B: 用 JSON 文件

## 存储格式

- [ ] A: 单文件
- [ ] B: 分片
"""


def _make_task(name: str, stage: str, body: str = "# stage\n"):
    d = TASKS / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    write_state(name, {
        "id": name, "stage": stage,
        "stage_idx": STAGES.index(stage), "stage_status": "running",
    })
    (d / f"{stage}.md").write_text(body, encoding="utf-8")
    return d


@pytest.fixture
def task():
    name = "pytest-mock-gate"
    _make_task(name, "01-brainstorming", CHOICE_MD)
    yield name
    shutil.rmtree(TASKS / name, ignore_errors=True)


@pytest.fixture
def review_task():
    name = "pytest-mock-gate-review"
    _make_task(name, "04-review")
    yield name
    shutil.rmtree(TASKS / name, ignore_errors=True)


def test_gate_is_signed_in_state(task):
    """签署要落在 .state 上，read_gate 必须认。"""
    assert not ss.read_gate(task, "01-brainstorming").signed

    _mock_gate_pass(task, "01-brainstorming")

    gate = ss.read_gate(task, "01-brainstorming")
    assert gate.signed, "门禁未签署 —— mock 大概又只改了 Markdown 的复选框"
    assert gate.pending == []


def test_choice_groups_get_decisions(task):
    """未拍板的选项组要补齐拍板记录，否则校验会一直报「尚未拍板」。

    数量要**刚好**等于选项组数：补记录的循环靠「真实校验不再抱怨」收敛，
    条件失效就会一路补到上限，凭空造出一堆用户从没做过的决定。
    """
    _mock_gate_pass(task, "01-brainstorming")

    assert ss.count_decisions(task, "01-brainstorming") == 2, \
        "拍板记录数应与选项组数一致，多出来说明补记录的循环没有正常收敛"


def test_real_compliance_check_passes(task):
    """终局断言：走生产校验，mock 之后不能再有任何待办。"""
    idx = STAGES.index("01-brainstorming")
    _, todo_before = check_stage_compliance(task, "01-brainstorming", idx)
    assert todo_before, "夹具本身就该有待办，否则这个用例证明不了什么"

    _mock_gate_pass(task, "01-brainstorming")

    _, todo_after = check_stage_compliance(task, "01-brainstorming", idx)
    assert todo_after == [], f"mock 后仍有待办，sw test 会被自己的门禁拦住: {todo_after}"


def test_review_route_is_written(review_task):
    """04-review 的 Route 要写进 .state，并让校验放行。"""
    _mock_gate_pass(review_task, "04-review")

    assert ss.read_route(review_task) == "05-archive"

    _, todo = check_stage_compliance(review_task, "04-review",
                                     STAGES.index("04-review"))
    assert todo == [], f"04-review 仍有待办: {todo}"


def test_is_idempotent(task):
    """重复调用不该无限堆拍板记录 —— 返工会让同一阶段被 mock 多次。"""
    _mock_gate_pass(task, "01-brainstorming")
    first = ss.count_decisions(task, "01-brainstorming")

    _mock_gate_pass(task, "01-brainstorming")

    assert ss.count_decisions(task, "01-brainstorming") == first


def test_survives_missing_stage_file(review_task):
    """阶段文件还没生成时不能抛异常，只需签好门禁。"""
    (TASKS / review_task / "04-review.md").unlink()

    _mock_gate_pass(review_task, "04-review")

    assert ss.read_gate(review_task, "04-review").signed
