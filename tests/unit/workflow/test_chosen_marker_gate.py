"""选项组已通过 `**Chosen**` 表达选择时，不得再判为未完成。

现场 bug（任务 iiiii）：agent 经 ask_user 与用户对齐后，用 `(Chosen)` 后缀 +
`- **Chosen**: xxx` 字段记录选择，但三个选项组的复选框仍是 `[ ]`。校验器只认
`[x]`，于是报「4 个待填项未完成」；用户按 [A] 批准只勾上 Gate，仍剩 3 个 ——
这 3 项用户无从下手，因为选择本身已经做过了。
"""
import shutil

import pytest

from sw_lib.core.config import TASKS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.utils import check_stage_compliance


@pytest.fixture
def task_name():
    """任务目录 + .state，且 Gate 已签署。

    本文件验的是「选项组的表达方式」，不是门禁签署。Gate 判定已改读 .state
    （docs/design-json-state-source.md），所以这里先把它签掉，让待办里只剩
    选项组相关项 —— 否则每个断言都会被未签署的门禁项干扰。
    """
    name = "pytest-chosen-marker"
    d = TASKS / name
    d.mkdir(parents=True, exist_ok=True)
    write_state(name, {"id": name, "stage": "01-brainstorming",
                       "stage_idx": 0, "stage_status": "running"})
    ss.sign_gate(name, "01-brainstorming")
    yield name
    shutil.rmtree(d, ignore_errors=True)


def _write(name, body):
    (TASKS / name / "01-brainstorming.md").write_text(body, encoding="utf-8")


_CHOSEN_FIELD = """# 01-Brainstorming

## Clarifying Questions (3)
1. **Topic**: 目标用户
   - [ ] A: C端消费者商城 — 面向普通消费者
   - [ ] B: B2B批发平台 — 面向企业客户 (Chosen)
   - [ ] C: 垂直领域商城 — 专注某一品类
   - **Chosen**: B2B批发平台

2. **Topic**: 核心功能
   - [ ] A: 全功能商城 — 含支付物流
   - [ ] B: 轻量询价模式 — 不含在线支付 (Chosen)
   - [ ] C: 简化版全功能 — 不含物流
   - **Chosen**: 轻量询价模式

## Gate
- [x] Design approved
- [x] Ready for Planning
"""


def test_chosen_field_satisfies_choice_group(task_name):
    """`- **Chosen**: xxx` 已表达选择，选项组不应再算待填。"""
    _write(task_name, _CHOSEN_FIELD)

    done, todo = check_stage_compliance(task_name, "01-brainstorming", 0)

    assert todo == [], f"已选择的选项组被误判: {todo}"


def test_chosen_suffix_alone_satisfies_choice_group(task_name):
    """只有 `(Chosen)` 后缀、没有独立 Chosen 字段时也应识别。"""
    body = "\n".join(l for l in _CHOSEN_FIELD.splitlines()
                     if not l.strip().startswith("- **Chosen**:"))
    _write(task_name, body)

    _, todo = check_stage_compliance(task_name, "01-brainstorming", 0)

    assert todo == [], f"(Chosen) 后缀未被识别: {todo}"


def test_chosen_field_alone_satisfies_choice_group(task_name):
    """只有 `- **Chosen**: xxx` 字段、选项行无 (Chosen) 后缀时也应识别。

    与上一个用例互补：两条识别路径必须各自独立生效，否则一条失效会被
    另一条掩盖。
    """
    _write(task_name, _CHOSEN_FIELD.replace(" (Chosen)", ""))

    _, todo = check_stage_compliance(task_name, "01-brainstorming", 0)

    assert todo == [], f"**Chosen** 字段未被识别: {todo}"


def test_unresolved_choice_group_still_blocks(task_name):
    """既没勾选、也没任何 Chosen 标记 → 仍须拦住。"""
    body = _CHOSEN_FIELD.replace(" (Chosen)", "")
    body = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("- **Chosen**:"))
    _write(task_name, body)

    _, todo = check_stage_compliance(task_name, "01-brainstorming", 0)

    assert any("选项组尚未拍板" in t for t in todo), todo


def test_chosen_placeholder_does_not_count(task_name):
    """`- **Chosen**: ___` 是未填占位符，不能当成已选择。"""
    body = _CHOSEN_FIELD.replace("- **Chosen**: B2B批发平台",
                                 "- **Chosen**: ___")
    body = body.replace("面向企业客户 (Chosen)", "面向企业客户")
    _write(task_name, body)

    _, todo = check_stage_compliance(task_name, "01-brainstorming", 0)

    assert any("选项组尚未拍板" in t for t in todo), \
        "占位符 ___ 不应被当作已选择"


def test_checkbox_selection_still_works(task_name):
    """传统 `[x]` 勾选方式必须继续有效。"""
    body = _CHOSEN_FIELD.replace("- [ ] B: B2B批发平台 — 面向企业客户 (Chosen)",
                                 "- [x] B: B2B批发平台 — 面向企业客户")
    body = body.replace("- [ ] B: 轻量询价模式 — 不含在线支付 (Chosen)",
                        "- [x] B: 轻量询价模式 — 不含在线支付")
    _write(task_name, body)

    _, todo = check_stage_compliance(task_name, "01-brainstorming", 0)

    assert todo == [], todo
