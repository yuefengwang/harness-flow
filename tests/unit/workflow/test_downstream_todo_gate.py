"""Task DAG 里留给下游阶段的待办不得计入本阶段门禁。

现场 bug：02-planning 的 Task DAG 有 12 个 `- [ ]` 任务（交给 03-coding 执行），
软校验把它们算作「待填项未完成」，导致 /advance 永久失败 —— 用户无法完成这个
校验，因为那些任务本阶段就不该勾选。
"""
import shutil

import pytest

from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.utils import check_stage_compliance


@pytest.fixture
def task_dir():
    """任务目录 + .state。Gate 由各用例按需签署。

    门禁判定已改读 .state（docs/design-json-state-source.md）：在 Markdown 里
    写 `- [x]` 不再影响结果，要表达「门禁已过」必须调 sign_gate。
    """
    name = "pytest-downstream-todo"
    d = TASKS / name
    d.mkdir(parents=True, exist_ok=True)
    write_state(name, {"id": name, "stage": "02-planning",
                       "stage_idx": 1, "stage_status": "running"})
    yield name, d
    shutil.rmtree(d, ignore_errors=True)


_PLANNING_WITH_OPEN_DAG = """# 02-Planning

## Task DAG

- [ ] **Task 1**: Backend Foundation | Deps: None
  - **Do**: Create app/main.py
  - **Verify**: `python -c "import app"`

- [ ] **Task 2**: Auth | Deps: Task 1
  - **Do**: Create services/auth.py
  - **Verify**: `pytest tests/test_auth.py`

## Test Strategy

- **Method**: unit
- **Key path**: register -> login

## Gate
- [x] Tests pass
- [x] No regression risk
"""


def test_open_task_dag_does_not_block_advance(task_dir):
    name, d = task_dir
    (d / "02-planning.md").write_text(_PLANNING_WITH_OPEN_DAG, encoding="utf-8")
    ss.sign_gate(name, "02-planning")

    done, todo = check_stage_compliance(name, "02-planning", 1)

    assert todo == [], f"Task DAG 待办被误判为门禁项: {todo}"
    assert any("门禁已签署" in d_ for d_ in done), done


def test_unsigned_gate_still_blocks(task_dir):
    """排除 Task DAG 不能顺带放过真正的 Gate —— 未签署必须拦住。"""
    name, d = task_dir
    (d / "02-planning.md").write_text(_PLANNING_WITH_OPEN_DAG, encoding="utf-8")
    # 故意不签 Gate

    _, todo = check_stage_compliance(name, "02-planning", 1)

    assert any("门禁项待签署" in t for t in todo), todo


def test_markdown_checkboxes_cannot_substitute_for_signature(task_dir):
    """Markdown 里写满 `[x]` 不能顶替签署。

    这是新状态源的核心保证：判定只读 .state。旧实现靠在文件里找 ``## Gate``
    定位区域，而 agent 的产出可以包含同样的标记 —— 那是整类歧义 bug 的来源。
    """
    name, d = task_dir
    (d / "02-planning.md").write_text(_PLANNING_WITH_OPEN_DAG, encoding="utf-8")
    # 文件里 Gate 两项都是 [x]，但 .state 未签署

    _, todo = check_stage_compliance(name, "02-planning", 1)

    assert any("门禁项待签署" in t for t in todo), \
        "Markdown 的 [x] 被当成了签署"


def test_non_gate_process_checkbox_does_not_block(task_dir):
    """Gate 之外的过程记录复选框不是门禁项 —— 用户无从代替 agent 判断。"""
    name, d = task_dir
    content = _PLANNING_WITH_OPEN_DAG.replace(
        "- **Method**: unit", "- [ ] Method decided")
    (d / "02-planning.md").write_text(content, encoding="utf-8")
    ss.sign_gate(name, "02-planning")

    _, todo = check_stage_compliance(name, "02-planning", 1)

    assert todo == [], f"Gate 之外的过程项被当成门禁: {todo}"


def test_choice_group_without_selection_still_blocks(task_dir):
    """01-brainstorming 的 A/B 方案没选仍必须拦住 —— 那是用户要做的决定，
    与「下游待办」性质不同。"""
    name, d = task_dir
    (d / "01-brainstorming.md").write_text(
        "# 01-Brainstorming\n\n"
        "## Clarifying Questions\n"
        "1. **Topic**: ___\n"
        "   - [ ] A: Option A — Pros\n"
        "   - [ ] B: Option B — Pros\n"
        "\n"
        "## Gate\n"
        "- [x] Design approved\n"
        "- [x] Ready for Planning\n",
        encoding="utf-8")
    ss.sign_gate(name, "01-brainstorming")

    _, todo = check_stage_compliance(name, "01-brainstorming", 0)

    assert any("选项组尚未拍板" in t for t in todo), todo
