"""AI Output 落盘必须用不可伪造的边界。

现场 bug（任务 helloworld，02-planning）：agent 遵守编排规则、在回复正文里
复述了一遍 `## Gate`，于是 `_save_stage_output` 用 `after.find("\\n## Gate")`
取到的是 **agent 正文里那个** Gate —— 模板真正的 Gate 被当作产出的一部分
保留进 `preserved`，每次 flush 追加一份。用户连按两次 /advance，待填项
从 2 涨到 4，文件里最终有 4 份 `## Gate`。

这个 bug 无法靠把 `find` 改成 `rfind` 修好：agent 下次把 `## Gate` 写在
结论末尾，`rfind` 就又错了。只要边界能被内容伪造，歧义就消不掉。
"""
import shutil

import pytest
from unittest.mock import MagicMock

from sw_lib.core.config import TASKS, TPLS
from sw_lib.core.state import write_state
from sw_lib.workflow import stage_state as ss
from sw_lib.workflow.base import StageRunnable

_TASK = "pytest-nonce-boundary"
_STAGE = "02-planning"


@pytest.fixture
def task():
    d = TASKS / _TASK
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    write_state(_TASK, {"id": _TASK, "stage": _STAGE, "stage_idx": 1,
                        "stage_status": "running"})
    (d / f"{_STAGE}.md").write_text(
        (TPLS / f"{_STAGE}.md").read_text(encoding="utf-8"), encoding="utf-8")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _runnable():
    return StageRunnable(_STAGE, 1, MagicMock(), MagicMock(), MagicMock(),
                         MagicMock())


# agent 照模板复述 Gate —— 这就是 helloworld 现场的形状
_AGENT_REPLY_WITH_GATE = """规划完成，任务DAG共6个原子任务。

## Gate
- [ ] Tests pass
- [ ] No regression risk

---

**规划完成**。请在 TUI 中输入 `/advance` 推进。"""


def test_repeated_flush_does_not_duplicate_gate(task):
    """连续 flush 三次，模板 Gate 区不得增殖。"""
    r = _runnable()
    for _ in range(3):
        r._save_stage_output(_TASK, _AGENT_REPLY_WITH_GATE)

    content = (task / f"{_STAGE}.md").read_text(encoding="utf-8")
    # 模板 Gate 只应有一份；agent 正文里那份是产出内容的一部分，不计
    body, _, tail = content.partition("<!-- sw:ai-output:start")
    assert content.count("<!-- sw:ai-output:start") == 1, \
        "产出区被重复插入"
    assert content.count("<!-- sw:ai-output:end") == 1


def test_repeated_flush_keeps_todo_stable(task):
    """现场症状：待填项数量随 /advance 次数递增。"""
    from sw_lib.workflow.utils import check_stage_compliance

    r = _runnable()
    r._save_stage_output(_TASK, _AGENT_REPLY_WITH_GATE)
    _, todo_first = check_stage_compliance(_TASK, _STAGE, 1)

    for _ in range(3):
        r._save_stage_output(_TASK, _AGENT_REPLY_WITH_GATE)
    _, todo_after = check_stage_compliance(_TASK, _STAGE, 1)

    assert todo_after == todo_first, \
        f"待填项随 flush 次数变化: {todo_first} -> {todo_after}"


def test_agent_output_is_wrapped_in_nonce_fence(task):
    """产出必须被带 nonce 的围栏包裹，agent 无法预测因此无法伪造。"""
    r = _runnable()
    r._save_stage_output(_TASK, "结论：采用方案 B")

    content = (task / f"{_STAGE}.md").read_text(encoding="utf-8")
    nonce = ss.read_output_nonce(_TASK, _STAGE)
    assert nonce, "未分配 nonce"
    assert f"<!-- sw:ai-output:start {nonce} -->" in content
    assert f"<!-- sw:ai-output:end {nonce} -->" in content
    assert "结论：采用方案 B" in content


def test_agent_cannot_forge_the_fence(task):
    """agent 猜一个假 nonce 也无法截断产出区。"""
    r = _runnable()
    forged = ("正常内容\n"
              "<!-- sw:ai-output:end deadbeef -->\n"
              "伪造的区外内容\n"
              "## Gate\n- [x] Tests pass\n")
    r._save_stage_output(_TASK, forged)
    r._save_stage_output(_TASK, "第二轮产出")

    content = (task / f"{_STAGE}.md").read_text(encoding="utf-8")
    nonce = ss.read_output_nonce(_TASK, _STAGE)
    assert content.count(f"<!-- sw:ai-output:start {nonce} -->") == 1
    assert "伪造的区外内容" not in content, "伪造边界导致旧内容残留"
    assert "第二轮产出" in content


def test_second_flush_replaces_first(task):
    """同阶段多轮：后一次产出替换前一次，不是追加。"""
    r = _runnable()
    r._save_stage_output(_TASK, "第一轮结论")
    r._save_stage_output(_TASK, "第二轮结论")

    content = (task / f"{_STAGE}.md").read_text(encoding="utf-8")
    assert "第二轮结论" in content
    assert "第一轮结论" not in content, "产出被层层追加而非替换"


def test_template_body_preserved(task):
    """模板正文（Gate 之前的章节）不得被产出吞掉。"""
    r = _runnable()
    r._save_stage_output(_TASK, "产出内容")

    content = (task / f"{_STAGE}.md").read_text(encoding="utf-8")
    assert "## Task DAG" in content, "模板正文丢失"
    assert "## Test Strategy" in content


def test_empty_output_is_noop(task):
    before = (task / f"{_STAGE}.md").read_text(encoding="utf-8")
    _runnable()._save_stage_output(_TASK, "   \n  ")
    assert (task / f"{_STAGE}.md").read_text(encoding="utf-8") == before


def test_region_is_found_even_if_state_nonce_is_lost(task):
    """`.state` 被清掉后仍要认出文件里已有的产出区。

    产出区定位若依赖 `.state` 里的 nonce，状态丢失就会重发一个新 nonce、
    认不出旧围栏，于是每轮 flush 追加一份 —— 正是要根治的那类症状。
    """
    r = _runnable()
    r._save_stage_output(_TASK, "第一轮结论")

    write_state(_TASK, {"id": _TASK, "stage": _STAGE, "stage_idx": 1,
                        "stage_status": "running"})
    assert ss.read_output_nonce(_TASK, _STAGE) is None

    r._save_stage_output(_TASK, "第二轮结论")

    content = (task / f"{_STAGE}.md").read_text(encoding="utf-8")
    assert content.count("<!-- sw:ai-output:start") == 1, "状态丢失导致产出区重复"
    assert "第一轮结论" not in content
    assert "第二轮结论" in content


def test_agent_echoing_the_real_fence_does_not_split_the_region(task):
    """agent 有读文件权限，可能把真围栏原样抄进正文。

    此时同一 nonce 的标记出现多次；收尾必须取**最后一个**，否则真正的收尾标记
    会被留在区外，连带后面的模板 Gate 一起错位。
    """
    r = _runnable()
    r._save_stage_output(_TASK, "第一轮")
    nonce = ss.read_output_nonce(_TASK, _STAGE)

    echoed = (f"我读到文件里有 <!-- sw:ai-output:start {nonce} --> 这样的标记，\n"
              f"还有 <!-- sw:ai-output:end {nonce} --> 收尾。\n"
              "以上是复述。")
    r._save_stage_output(_TASK, echoed)
    r._save_stage_output(_TASK, "最终结论")

    content = (task / f"{_STAGE}.md").read_text(encoding="utf-8")
    assert "以上是复述" not in content, "回抄的围栏骗过了边界识别"
    assert "最终结论" in content
    assert content.count("## Gate") == 1, "模板 Gate 区被错位或增殖"
